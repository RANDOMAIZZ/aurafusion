// HostLink.java - заготовка Fabric-мода 1.21.1 (порт mc/ из примера под Forge->Fabric).
// Полный мод собирается через Fabric Loom + JDK21, здесь только логика линка.
// Ставить: отдельный профиль Fabric 1.21.1, game dir C:\dev\passthrough\mcgame, БЕЗ OptiFine.
// Протокол: ws://127.0.0.1:25599, сообщения cam / ground (см. protocol.json).
package dev.mw.passthrough;

import java.net.InetSocketAddress;
import java.util.concurrent.ConcurrentHashMap;
import org.java_websocket.server.WebSocketServer;
import org.java_websocket.WebSocket;
import org.java_websocket.handshake.ClientHandshake;
// Зависимость: org.java-websocket:Java-WebSocket:1.5.6 (как в примере)

public class HostLink extends WebSocketServer {
    public static volatile double camX=0, camY=67, camZ=0, yaw=0, pitch=-10;
    public static ConcurrentHashMap<String,Integer> ground = new ConcurrentHashMap<>();

    public HostLink() { super(new InetSocketAddress("127.0.0.1", 25599)); }

    @Override public void onOpen(WebSocket c, ClientHandshake h) {}
    @Override public void onClose(WebSocket c, int code, String r, boolean rem) {}
    @Override public void onError(WebSocket c, Exception e) {}

    @Override public void onMessage(WebSocket c, String msg) {
        // ultralight-json: парсим только нужные поля без Gson чтобы не тянуть deps
        if (msg.contains("\"t\":\"cam\"")) {
            camX = num(msg, "\"pos\":[", 0); camY = num(msg, "\"pos\":[", 1); camZ = num(msg, "\"pos\":[", 2);
            yaw = num(msg, "\"yaw\":", 0); pitch = num(msg, "\"pitch\":", 0);
            // TODO: PlayerSync - поставить камеру Стива на camX,camY,camZ, yaw=180-heading
        } else if (msg.contains("\"t\":\"ground\"")) {
            // TODO: WorldBridge - залить barrier-блоки по cols, yOffset подобрать как в примере (F8 re-level)
            ground.put("n", ground.getOrDefault("n", 0) + 1);
        }
    }
    static double num(String s, String key, int idx) {
        try {
            int p = s.indexOf(key) + key.length();
            String sub = s.substring(p, Math.min(p + 128, s.length())).replaceAll("[\\[\\] ]", "");
            return Double.parseDouble(sub.split(",")[idx].split("[}]")[0]);
        } catch (Exception e) { return 0; }
    }
    // TODO: FrameExporter (shared memory Local\MCPassthroughFrame) - только после слайса 1,
    // для DX9 NFS понадобится отдельный ReShade-эффект, не MCPassthrough.fx из GTA-примера.
}
