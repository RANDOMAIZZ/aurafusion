// WorldBridge.java - земля NFS -> barrier-блоки, блоки MC -> пропы NFS.
// Порт WorldBridge из примера minecraft-gta5-passthrough под MW.
// Требует Fabric 1.21.1 + JDK21. Пока без сборки, логика готова к вставке в мод.
package dev.mw.passthrough;

import java.util.Map;
import java.util.HashMap;

public class WorldBridge {
    // NFS (x,y,z) -> MC (x, z+yOffset, -y), 1 метр = 1 блок
    public static int[] nfsToMc(double nx, double ny, double nz, int yOffset) {
        return new int[]{(int)Math.floor(nx), (int)Math.floor(nz) + yOffset, (int)Math.floor(-ny)};
    }
    // yOffset подбираем как в примере: земля под ногами на целом блоке, F8 re-level
    public static int pickYOffset(double feetNFS, int mcGroundY) {
        return mcGroundY - (int)Math.floor(feetNFS);
    }
    // cols из {"t":"ground"} -> map "x,z" -> y. Лимит 400 пропов как в примере (GTA крашится ~1500).
    public static Map<String,Integer> lastGround = new HashMap<>();
    public static void applyGround(java.util.List<Map<String,Object>> cols, int yOffset) {
        lastGround.clear();
        int n = 0;
        for (Map<String,Object> c : cols) {
            if (n++ >= 400) break;
            int bx = ((Number)c.get("x")).intValue();
            int bz = ((Number)c.get("z")).intValue();
            int by = ((Number)c.get("y")).intValue() + yOffset;
            lastGround.put(bx + "," + bz, by);
            // TODO(Fabric): world.setBlockState(new BlockPos(bx,by,bz), Blocks.BARRIER.getDefaultState(), 18);
        }
    }
    // Блок поставлен/сломан в MC -> JSON для ASI: {"t":"block","pos":[x,y,z],"place":true}
    public static String blockEvent(int x, int y, int z, boolean place) {
        return "{\"t\":\"block\",\"pos\":[" + x + "," + y + "," + z + "],\"place\":" + place + "}";
    }
}
