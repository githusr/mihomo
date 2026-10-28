#!/usr/bin/env python3
"""Run the real observer's selection/publication code against fake Android services.

These host tests do not exercise Binder, SELinux or Android callback delivery.
"""

import os
from pathlib import Path
import subprocess
import tempfile


SOURCES = {
    "android/content/Context.java": """
package android.content;
public class Context {
    public static final String CONNECTIVITY_SERVICE = "connectivity";
    public Object service;
    public <T> T getSystemService(Class<T> type) { return type.cast(service); }
}
""",
    "android/os/HandlerThread.java": """
package android.os;
public class HandlerThread extends Thread {
    public HandlerThread(String name) { super(name); }
    public Looper getLooper() { return null; }
}
""",
    "android/net/Network.java": """
package android.net;
public class Network {
    private final long handle;
    public Network(long handle) { this.handle = handle; }
    public long getNetworkHandle() { return handle; }
    public boolean equals(Object other) {
        return other instanceof Network && ((Network) other).handle == handle;
    }
    public int hashCode() { return Long.hashCode(handle); }
}
""",
    "android/net/NetworkCapabilities.java": """
package android.net;
public class NetworkCapabilities {
    public static final int NET_CAPABILITY_INTERNET = 12, NET_CAPABILITY_NOT_RESTRICTED = 13,
        NET_CAPABILITY_TRUSTED = 14, NET_CAPABILITY_NOT_VPN = 15, NET_CAPABILITY_VALIDATED = 16;
    public boolean vpn;
    public boolean hasCapability(int value) { return value != NET_CAPABILITY_NOT_VPN || !vpn; }
}
""",
    "android/net/LinkProperties.java": """
package android.net;
public class LinkProperties {
    private final java.util.List<java.net.InetAddress> servers;
    public LinkProperties(String address) throws Exception {
        servers = java.util.Collections.singletonList(java.net.InetAddress.getByName(address));
    }
    public java.util.List<java.net.InetAddress> getDnsServers() { return servers; }
}
""",
    "android/net/ConnectivityManager.java": """
package android.net;
public class ConnectivityManager {
    public Network active;
    public Network[] current;
    public final java.util.Map<Network, NetworkCapabilities> caps = new java.util.HashMap<>();
    public final java.util.Map<Network, LinkProperties> links = new java.util.HashMap<>();
    public Network getActiveNetwork() { return active; }
    public Network[] getAllNetworks() { return current; }
    public NetworkCapabilities getNetworkCapabilities(Network n) { return caps.get(n); }
    public LinkProperties getLinkProperties(Network n) { return links.get(n); }
    public void registerNetworkCallback(NetworkRequest r, NetworkCallback c, android.os.Handler h) { }
    public void registerDefaultNetworkCallback(NetworkCallback c, android.os.Handler h) { }
    public static class NetworkCallback {
        public void onAvailable(Network n) { }
        public void onLost(Network n) { }
        public void onCapabilitiesChanged(Network n, NetworkCapabilities c) { }
        public void onLinkPropertiesChanged(Network n, LinkProperties p) { }
    }
}
""",
    "org/json/JSONArray.java": """
package org.json;
public class JSONArray {
    private final java.util.Collection<?> values;
    public JSONArray(java.util.Collection<?> values) { this.values = values; }
    public String toString() {
        return values.stream().map(v -> "\\\"" + v + "\\\"")
            .collect(java.util.stream.Collectors.joining(",", "[", "]"));
    }
}
""",
    "org/json/JSONObject.java": """
package org.json;
public class JSONObject {
    private final java.util.Map<String, Object> values = new java.util.LinkedHashMap<>();
    public JSONObject put(String key, Object value) { values.put(key, value); return this; }
    public String toString() {
        return values.entrySet().stream().map(e -> "\\\"" + e.getKey() + "\\\":" + e.getValue())
            .collect(java.util.stream.Collectors.joining(",", "{", "}"));
    }
}
""",
    "ObserverTest.java": """
import android.content.Context;
import android.net.*;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import mihomo.systemdns.SystemDnsHelper;

public class ObserverTest {
    public static void main(String[] args) throws Exception {
        ConnectivityManager cm = new ConnectivityManager();
        Context context = new Context();
        context.service = cm;
        Constructor<SystemDnsHelper> constructor = SystemDnsHelper.class.getDeclaredConstructor(Context.class);
        constructor.setAccessible(true);
        Object helper = constructor.newInstance(context);
        Method publish = SystemDnsHelper.class.getDeclaredMethod("publish");
        publish.setAccessible(true);
        Field generation = SystemDnsHelper.class.getDeclaredField("generation");
        Field snapshot = SystemDnsHelper.class.getDeclaredField("snapshot");
        generation.setAccessible(true);
        snapshot.setAccessible(true);
        Network a = new Network(100), b = new Network(101), vpn = new Network(102);
        for (Network n : new Network[]{a, b, vpn}) {
            cm.caps.put(n, new NetworkCapabilities());
            cm.links.put(n, new LinkProperties("192.0.2.1"));
        }
        cm.caps.get(vpn).vpn = true;
        cm.current = new Network[]{a, b};
        cm.active = a;
        publish.invoke(helper);
        byte[] first = (byte[]) snapshot.get(helper);
        assert generation.getLong(helper) == 1;
        publish.invoke(helper);
        assert generation.getLong(helper) == 1 : "unchanged callbacks should be coalesced";
        cm.active = b;
        publish.invoke(helper);
        assert generation.getLong(helper) == 2 : "same-DNS network handover was lost";
        assert Arrays.equals(first, (byte[]) snapshot.get(helper)) : "the address protocol changed";
        cm.links.put(b, new LinkProperties("192.0.2.2"));
        publish.invoke(helper);
        assert generation.getLong(helper) == 3 : "DNS-only change was lost";
        // Startup scans can see a network that later disappears without a matching onLost.
        cm.current = new Network[]{a, vpn};
        cm.active = vpn;
        publish.invoke(helper);
        assert generation.getLong(helper) == 4;
        assert Arrays.equals(first, (byte[]) snapshot.get(helper)) : "stale startup DNS was retained";
        cm.current = new Network[]{vpn};
        publish.invoke(helper);
        assert new String((byte[]) snapshot.get(helper), StandardCharsets.UTF_8).contains("\\\"servers\\\":[]")
            : "VPN/offline state did not withdraw physical DNS";
        System.out.println("PASS: same-DNS handover, duplicate callback, DNS change, stale network and VPN/offline");
    }
}
""",
}


def main():
    root = Path(__file__).resolve().parent
    android = Path(os.environ["ANDROID_HOME"]) / "platforms/android-36/android.jar"
    java_bin = Path(os.environ["JAVA_HOME"]) / "bin" if os.environ.get("JAVA_HOME") else Path()
    with tempfile.TemporaryDirectory(prefix="mihomo-observer-test-") as tmp:
        tmp = Path(tmp)
        sources = []
        for name, source in SOURCES.items():
            path = tmp / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
            sources.append(str(path))
        classes = tmp / "classes"
        classes.mkdir()
        subprocess.run([str(java_bin / "javac"), "--release", "8", "-Xlint:-options", "-cp", str(android),
                        "-d", str(classes), *sources, str(root / "java/SystemDnsHelper.java")], check=True)
        subprocess.run([str(java_bin / "java"), "-ea", "-cp", str(classes) + os.pathsep + str(android),
                        "ObserverTest"], check=True)


if __name__ == "__main__":
    main()
