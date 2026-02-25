import type { ClientEvent, ServerEvent } from "@portfolio/shared-types";

export type ServerEventHandler = (event: ServerEvent) => void;

export class VoiceWsClient {
  private ws: WebSocket | null = null;
  private onOpenCb: (() => void) | null = null;
  private onCloseCb: ((event: CloseEvent) => void) | null = null;
  private onErrorCb: ((event: Event) => void) | null = null;

  constructor(private readonly url: string, private readonly onEvent: ServerEventHandler) {}

  connect() {
    if (this.ws && this.ws.readyState <= 1) return;
    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      this.onOpenCb?.();
    };

    this.ws.onclose = (event) => {
      this.onCloseCb?.(event);
    };

    this.ws.onmessage = (msg) => {
      try {
        const payload = JSON.parse(msg.data) as ServerEvent;
        this.onEvent(payload);
      } catch {
        // ignore malformed frames
      }
    };

    this.ws.onerror = (event) => {
      this.onErrorCb?.(event);
    };
  }

  send(event: ClientEvent) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(event));
  }

  onOpen(cb: () => void) {
    this.onOpenCb = cb;
  }

  onClose(cb: (event: CloseEvent) => void) {
    this.onCloseCb = cb;
  }

  onError(cb: (event: Event) => void) {
    this.onErrorCb = cb;
  }

  close() {
    this.ws?.close();
    this.ws = null;
  }
}
