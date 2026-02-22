// Penpot MCP Plugin — Worker
// Load via Penpot: Main Menu → Plugin Manager → http://localhost:8787/plugin/manifest.json
//
// Architecture:
//   plugin.js (this file) runs in the Penpot plugin sandbox — has penpot.* API but NO WebSocket.
//   ui.html (iframe)      runs in a normal browser context — has WebSocket, fetch, etc.
//   Communication:  plugin.js <-> penpot.ui.sendMessage/onMessage <-> ui.html <-> ws://localhost:4402

(function () {
  // Open the UI panel — WebSocket lives here
  penpot.ui.open("Penpot MCP", "plugin/ui.html", { width: 280, height: 200 });

  // Forward canvas selection changes to UI panel (which relays to WebSocket server)
  penpot.on("selectionchange", function () {
    var ids = penpot.selection.map(function (shape) {
      return shape.id;
    });
    penpot.ui.sendMessage({ type: "selectionchange", ids: ids });
  });

  // Handle messages from the UI panel (WebSocket server → ui.html → here)
  penpot.ui.onMessage(function (msg) {
    if (!msg || !msg.type) return;

    if (msg.type === "execute") {
      // Execute script with penpot injected as argument
      try {
        var fn = new Function("penpot", msg.script);
        fn(penpot);
        penpot.ui.sendMessage({
          type: "ack",
          command_id: msg.command_id,
          status: "ok",
        });
      } catch (err) {
        penpot.ui.sendMessage({
          type: "ack",
          command_id: msg.command_id,
          status: "error",
          message: err.message,
        });
      }
    }
  });
})();
