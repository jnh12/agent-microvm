package io.github.jnh12.vmnest;

import java.net.Socket;
import java.net.http.HttpClient;
import java.security.GeneralSecurityException;
import java.security.cert.X509Certificate;
import java.time.Duration;
import javax.net.ssl.*;

final class Tls {
  private Tls() {}

  static SSLContext context(boolean verify) {
    try {
      if (verify) return SSLContext.getDefault();
      // Explicit per-client opt-out, matching Python's verify_tls=False; never changes JVM
      // defaults.
      var trust =
          new X509ExtendedTrustManager() {
            public X509Certificate[] getAcceptedIssuers() {
              return new X509Certificate[0];
            }

            public void checkClientTrusted(X509Certificate[] chain, String auth) {}

            public void checkServerTrusted(X509Certificate[] chain, String auth) {}

            public void checkClientTrusted(X509Certificate[] chain, String auth, Socket socket) {}

            public void checkServerTrusted(X509Certificate[] chain, String auth, Socket socket) {}

            public void checkClientTrusted(
                X509Certificate[] chain, String auth, SSLEngine engine) {}

            public void checkServerTrusted(
                X509Certificate[] chain, String auth, SSLEngine engine) {}
          };
      var context = SSLContext.getInstance("TLS");
      context.init(null, new TrustManager[] {trust}, null);
      return context;
    } catch (GeneralSecurityException e) {
      throw new IllegalStateException("TLS initialization failed", e);
    }
  }

  static HttpClient client(boolean verify) {
    return HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(10))
        .sslContext(context(verify))
        .followRedirects(HttpClient.Redirect.NEVER)
        .build();
  }
}
