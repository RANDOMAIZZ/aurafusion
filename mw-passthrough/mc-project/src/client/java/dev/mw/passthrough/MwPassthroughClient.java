package dev.mw.passthrough;

import net.fabricmc.api.ClientModInitializer;
import net.minecraft.block.Blocks;
import net.minecraft.util.math.BlockPos;
import org.java_websocket.server.WebSocketServer;
import org.java_websocket.WebSocket;
import org.java_websocket.handshake.ClientHandshake;
import java.net.InetSocketAddress;
import java.util.*;

// Слайс 1: принимает cam+ground с NFS ASI / fakehost, ставит barrier-землю.
// Тик привозит Mixin (ClientTickMixin) - без зависимости fabric-api.
public class MwPassthroughClient implements ClientModInitializer {
  public static volatile double camX=0, camY=67, camZ=0, yaw=0, pitch=-10;
  public static volatile List<Map<String,Double>> cols = new ArrayList<>();
  public static volatile boolean dirty = false;

  static int dbgMsg = 0;
  static class Link extends WebSocketServer {
    Link(){ super(new InetSocketAddress("127.0.0.1", 25599)); }
    public void onOpen(WebSocket c, ClientHandshake h){}
    public void onClose(WebSocket c, int k, String r, boolean x){}
    public void onError(WebSocket c, Exception e){}
    public void onStart(){}
    public void onMessage(WebSocket c, String raw){
      String m = raw.replace(" ","");
      try {
        if(m.contains("\"t\":\"cam\"")){
          camX = num(m,"\"pos\":[",0); camY = num(m,"\"pos\":[",1); camZ = num(m,"\"pos\":[",2);
          yaw = num(m,"\"yaw\":",0); pitch = num(m,"\"pitch\":",0);
        } else if(m.contains("\"t\":\"ground\"")){
          cols = parseCols(m); dirty = true;
          if(dbgMsg++ % 300 == 0) System.out.println("[mw-passthrough] ground cols=" + cols.size());
        }
      } catch(Exception ignored){}
    }
    static double num(String s, String k, int i){
      int p = s.indexOf(k)+k.length();
      String sub = s.substring(p, Math.min(p+160,s.length())).replaceAll("[\\[\\] ]","");
      return Double.parseDouble(sub.split(",")[i].split("[}]")[0]);
    }
    static List<Map<String,Double>> parseCols(String m){
      List<Map<String,Double>> out = new ArrayList<>();
      int a = m.indexOf("\"cols\":["); if(a<0) return out;
      String cut = m.substring(a+8);
      java.util.regex.Matcher mm = java.util.regex.Pattern.compile("\\{\"x\":(-?\\d+),\"z\":(-?\\d+),\"y\":(-?\\d+)\\}").matcher(cut);
      int n=0;
      while(mm.find() && n++<400){
        Map<String,Double> e = new HashMap<>();
        e.put("x",(double)Integer.parseInt(mm.group(1)));
        e.put("z",(double)Integer.parseInt(mm.group(2)));
        e.put("y",(double)Integer.parseInt(mm.group(3)));
        out.add(e);
      }
      return out;
    }
  }

  @Override public void onInitializeClient(){
    Link link = new Link(); link.start();
    System.out.println("[mw-passthrough] link on 127.0.0.1:25599");
  }

  // зовется из миксина каждый тик клиента
  public static int dbgTick = 0;
  public static void onTick(net.minecraft.client.MinecraftClient client){
    if(dbgTick++ == 1) System.out.println("[mw-passthrough] tick alive");
    if(!dirty || client.world==null || client.player==null) return;
    dirty = false;
    int yOffset = 64 - (int)Math.floor(camY-1);
    int baseX = (int)Math.floor(camX), baseZ = (int)Math.floor(camZ);
    int placed = 0; String first = "";
    for(Map<String,Double> c : cols){
      int bx = c.get("x").intValue() + baseX;
      int bz = c.get("z").intValue() + baseZ;
      int by = c.get("y").intValue() + yOffset;
      try { client.world.setBlockState(new BlockPos(bx, by, bz), Blocks.BARRIER.getDefaultState(), 18); placed++; if(first.isEmpty()) first = bx+","+by+","+bz; }
      catch(Exception e){ System.out.println("[mw-passthrough] setBlock FAIL " + e); }
    }
    System.out.println("[mw-passthrough] placed=" + placed + " first=" + first);
  }
}
