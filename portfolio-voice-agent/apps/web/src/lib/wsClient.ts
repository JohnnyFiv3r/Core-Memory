import type { ClientEvent, ServerEvent } from "@portfolio/shared-types";

export type ServerEventHandler = (event: ServerEvent) => void;

export class VoiceWsClient {
  private ws: WebSocket | null = null;
  private onOpenCb: (() => void) | null = null;

  constructor(private readonly url: string, private readonly onEvent: ServerEventHandler) {}

  connect() {
    if (this.ws && this.ws.readyState <= 1) return;
    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      this.onOpenCb?.();
    };

    this.ws.onmessage = (msg) => {
      try {
        const payload = JSON.parse(msg.data) as ServerEvent;
        this.onEvent(payload);
      } catch {
        // ignore malformed frames
      }
    };
  }

  send(event: ClientEvent) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(event));
  }

  onOpen(cb: () => void) {
    this.onOpenCb = cb;
  }

  close() {
    this.ws?.close();
    this.ws = null;
  }
}
