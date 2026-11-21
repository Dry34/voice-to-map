// Connexion WebSocket avec reconnexion automatique. Responsable : A.

export function connect({ onMessage, onStatus, onOpen }) {
  let delay = 500;
  let ws = null;

  function open() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);

    ws.onopen = () => {
      delay = 500;
      onStatus(true);
      onOpen?.();
    };
    ws.onmessage = (e) => onMessage(JSON.parse(e.data));
    ws.onclose = () => {
      onStatus(false);
      setTimeout(open, delay);
      delay = Math.min(delay * 2, 8000);
    };
  }

  open();
  return {
    send(msg) {
      if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
    },
  };
}
