package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHubStatsAndPendingCorrelation(t *testing.T) {
	hub := NewHub("", "")
	if hub.ClientCount() != 0 {
		t.Fatalf("expected 0 clients, got %d", hub.ClientCount())
	}

	ch := hub.RegisterPending("req-123")
	ok := hub.ResolvePending("req-123", json.RawMessage(`{"status":"ok"}`))
	if !ok {
		t.Fatal("expected ResolvePending to return true")
	}
	select {
	case msg := <-ch:
		if string(msg) != `{"status":"ok"}` {
			t.Fatalf("unexpected payload: %s", string(msg))
		}
	default:
		t.Fatal("expected correlated message on channel")
	}

	req := httptest.NewRequest(http.MethodGet, "/ws/stats", nil)
	rec := httptest.NewRecorder()
	hub.handleStats(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("invalid json: %v", err)
	}
	if body["engine"] != "go-ws-hub" {
		t.Fatalf("expected engine=go-ws-hub, got %v", body["engine"])
	}
}
