package main

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/coder/websocket"
)

type Client struct {
	id   string
	conn *websocket.Conn
	send chan []byte
}

type Hub struct {
	mu          sync.RWMutex
	clients     map[*Client]struct{}
	pending     map[string]chan json.RawMessage
	startedAt   time.Time
	telemetryTo string
	wsToken     string
	httpClient  *http.Client
}

func NewHub(telemetryURL, wsToken string) *Hub {
	return &Hub{
		clients:     make(map[*Client]struct{}),
		pending:     make(map[string]chan json.RawMessage),
		startedAt:   time.Now(),
		telemetryTo: strings.TrimSpace(telemetryURL),
		wsToken:     strings.TrimSpace(wsToken),
		httpClient:  &http.Client{Timeout: 3 * time.Second},
	}
}

func (h *Hub) ClientCount() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.clients)
}

func (h *Hub) Register(c *Client) {
	h.mu.Lock()
	h.clients[c] = struct{}{}
	h.mu.Unlock()
}

func (h *Hub) Unregister(c *Client) {
	h.mu.Lock()
	if _, ok := h.clients[c]; ok {
		delete(h.clients, c)
		close(c.send)
	}
	h.mu.Unlock()
}

func (h *Hub) Broadcast(msg []byte) int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	sent := 0
	for c := range h.clients {
		select {
		case c.send <- msg:
			sent++
		default:
		}
	}
	return sent
}

func (h *Hub) RegisterPending(reqID string) chan json.RawMessage {
	ch := make(chan json.RawMessage, 1)
	h.mu.Lock()
	h.pending[reqID] = ch
	h.mu.Unlock()
	return ch
}

func (h *Hub) ResolvePending(reqID string, payload json.RawMessage) bool {
	h.mu.Lock()
	ch, ok := h.pending[reqID]
	if ok {
		delete(h.pending, reqID)
	}
	h.mu.Unlock()
	if ok {
		select {
		case ch <- payload:
		default:
		}
		return true
	}
	return false
}

func (h *Hub) CancelPending(reqID string) {
	h.mu.Lock()
	delete(h.pending, reqID)
	h.mu.Unlock()
}

func (h *Hub) forwardTelemetry(raw []byte) {
	if h.telemetryTo == "" {
		return
	}
	req, err := http.NewRequest(http.MethodPost, h.telemetryTo, bytes.NewReader(raw))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := h.httpClient.Do(req)
	if err == nil {
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
	}
}

func (h *Hub) authorizeWS(r *http.Request) bool {
	if h.wsToken != "" {
		got := r.URL.Query().Get("token")
		if got == "" {
			got = strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		}
		if subtle.ConstantTimeCompare([]byte(got), []byte(h.wsToken)) != 1 {
			return false
		}
	}
	return true
}

func (h *Hub) handleWS(w http.ResponseWriter, r *http.Request) {
	if !h.authorizeWS(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		InsecureSkipVerify: true, // Origin enforced at Nginx reverse proxy
	})
	if err != nil {
		log.Printf("ws accept error: %v", err)
		return
	}

	client := &Client{
		id:   r.RemoteAddr,
		conn: conn,
		send: make(chan []byte, 64),
	}
	h.Register(client)
	defer func() {
		h.Unregister(client)
		_ = conn.Close(websocket.StatusNormalClosure, "closed")
	}()

	welcome, _ := json.Marshal(map[string]any{
		"type":    "ws_ready",
		"engine":  "go-ws-hub",
		"clients": h.ClientCount(),
	})
	client.send <- welcome

	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()

	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case msg, ok := <-client.send:
				if !ok {
					return
				}
				wctx, wcancel := context.WithTimeout(ctx, 5*time.Second)
				err := conn.Write(wctx, websocket.MessageText, msg)
				wcancel()
				if err != nil {
					cancel()
					return
				}
			}
		}
	}()

	for {
		_, data, err := conn.Read(ctx)
		if err != nil {
			return
		}
		var envelope map[string]json.RawMessage
		if json.Unmarshal(data, &envelope) == nil {
			if reqRaw, ok := envelope["request_id"]; ok {
				var reqID string
				if json.Unmarshal(reqRaw, &reqID) == nil && reqID != "" {
					h.ResolvePending(reqID, json.RawMessage(data))
				}
			}
		}
		go h.forwardTelemetry(data)
	}
}

func (h *Hub) handleStats(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"status":   "ok",
		"engine":   "go-ws-hub",
		"clients":  h.ClientCount(),
		"uptime_s": int(time.Since(h.startedAt).Seconds()),
	})
}

func (h *Hub) handleDispatch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	var payload map[string]any
	if err := json.Unmarshal(body, &payload); err != nil {
		http.Error(w, "invalid json", http.StatusBadRequest)
		return
	}

	reqID, _ := payload["request_id"].(string)
	awaitResult, _ := payload["await_result"].(bool)
	var ch chan json.RawMessage
	if awaitResult && reqID != "" {
		ch = h.RegisterPending(reqID)
		defer h.CancelPending(reqID)
	}

	sent := h.Broadcast(body)
	w.Header().Set("Content-Type", "application/json")
	if sent == 0 {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "no_clients",
			"sent":   0,
		})
		return
	}

	if ch == nil {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "dispatched",
			"sent":   sent,
		})
		return
	}

	select {
	case res := <-ch:
		_ = json.NewEncoder(w).Encode(res)
	case <-time.After(12 * time.Second):
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status":     "timeout",
			"request_id": reqID,
		})
	}
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "-healthcheck" {
		client := &http.Client{Timeout: 3 * time.Second}
		resp, err := client.Get("http://127.0.0.1:4402/ws/health")
		if err != nil || resp.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		_ = resp.Body.Close()
		os.Exit(0)
	}
	addr := os.Getenv("WS_GO_ADDR")
	if addr == "" {
		addr = ":4402"
	}
	telemetryURL := os.Getenv("MCP_TELEMETRY_URL")
	if telemetryURL == "" {
		telemetryURL = "http://penpot-mcp:8787/plugin/telemetry"
	}
	wsToken := os.Getenv("WS_TOKEN")

	hub := NewHub(telemetryURL, wsToken)
	mux := http.NewServeMux()
	mux.HandleFunc("/plugin/ws", hub.handleWS)
	mux.HandleFunc("/ws", hub.handleWS)
	mux.HandleFunc("/ws/health", hub.handleStats)
	mux.HandleFunc("/ws/stats", hub.handleStats)
	mux.HandleFunc("/ws/dispatch", hub.handleDispatch)

	srv := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}

	go func() {
		log.Printf("penpot-ws-go listening on %s (telemetry=%s)", addr, telemetryURL)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("server error: %v", err)
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = srv.Shutdown(ctx)
}
