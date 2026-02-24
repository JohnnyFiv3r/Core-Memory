export class VoiceWsClient {
    url;
    onEvent;
    ws = null;
    onOpenCb = null;
    onCloseCb = null;
    constructor(url, onEvent) {
        this.url = url;
        this.onEvent = onEvent;
    }
    connect() {
        if (this.ws && this.ws.readyState <= 1)
            return;
        this.ws = new WebSocket(this.url);
        this.ws.onopen = () => {
            this.onOpenCb?.();
        };
        this.ws.onclose = () => {
            this.onCloseCb?.();
        };
        this.ws.onmessage = (msg) => {
            try {
                const payload = JSON.parse(msg.data);
                this.onEvent(payload);
            }
            catch {
                // ignore malformed frames
            }
        };
    }
    send(event) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN)
            return;
        this.ws.send(JSON.stringify(event));
    }
    onOpen(cb) {
        this.onOpenCb = cb;
    }
    onClose(cb) {
        this.onCloseCb = cb;
    }
    close() {
        this.ws?.close();
        this.ws = null;
    }
}
