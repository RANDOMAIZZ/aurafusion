import org.java_websocket.client.WebSocketClient;
import org.java_websocket.handshake.ServerHandshake;
import java.net.URI;

// BridgeCheck - Java-сторона линка без Minecraft. Считает cam/ground с fakehost.
// Сборка: javac -cp Java-WebSocket.jar, запуск: java -cp .;Java-WebSocket.jar
public class BridgeCheck extends WebSocketClient {
  static int nCam=0, nGnd=0;
  public BridgeCheck(URI u){ super(u); }
  public void onOpen(ServerHandshake h){ System.out.println("connected"); send("{}"); }
  public void onMessage(String m){
    if(m.contains("\"t\":\"cam\"")){ nCam++; if(nCam%60==1) System.out.println("cam #"+nCam+" "+m.substring(0,Math.min(80,m.length()))); }
    else if(m.contains("\"t\":\"ground\"")){ nGnd++; if(nGnd==1) System.out.println("ground ok len="+m.length()); }
  }
  public void onClose(int c,String r,boolean x){ System.out.println("closed"); }
  public void onError(Exception e){ System.out.println("err "+e); }
  public static void main(String[] a) throws Exception {
    int secs = a.length>0 ? Integer.parseInt(a[0]) : 8;
    BridgeCheck c = new BridgeCheck(new URI("ws://127.0.0.1:25599"));
    c.connectBlocking();
    Thread.sleep(secs*1000L);
    c.closeBlocking();
    System.out.println("итог: cam="+nCam+" ground="+nGnd);
    System.out.println(nCam>10 ? "OK - Java-линк живой" : "мало cam");
  }
}
