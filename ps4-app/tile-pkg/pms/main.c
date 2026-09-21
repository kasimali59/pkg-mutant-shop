/* PKG MUTANT SHOP - the PS4 dashboard app.
 *
 * WHY THIS EXISTS. On the PS5 the shop puts a tile on the dashboard: a small package the ELF
 * installs on boot, whose only job is to open the shop's own page. Without the PS4 equivalent there
 * is nothing on the PS4 to press - the shop only exists while a payload happens to be injected, and
 * the jailbreak's payload loader does not survive rest mode. So a PS4 with the shop "installed" had
 * no way to reach it, which is exactly what a person sees as "the app is not there".
 *
 * WHAT IT DOES, in order:
 *   1. is the shop already answering on 127.0.0.1:8710?  -> just open it
 *   2. if not, hand the payload it carries to a payload loader and wait for the shop to come up
 *   3. open the console's browser at the shop
 *   4. if none of that worked, say so on screen in words, naming the thing that is missing
 *
 * WHY IT LOADS A PAYLOAD RATHER THAN BEING THE SERVER. An application is suspended the moment the
 * browser comes to the foreground, and a suspended process stops answering its socket - so a shop
 * served by THIS process would die at the exact moment the page tried to load. The payload lives in
 * a long-running system process instead, which is why it survives the browser, the app closing, and
 * everything else. This app is the button; the payload is the shop.
 *
 * NOTHING HERE IS PRIVILEGED. It opens loopback sockets, reads its own /app0, and calls three
 * public system-service functions. That is deliberate: an application runs sandboxed, and a tile
 * that needed more than a sandbox allows would be a tile that does not work.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <orbis/libkernel.h>
#include <orbis/SystemService.h>
#include <orbis/UserService.h>

#define SHOP_PORT     8710
#define PAYLOAD_PATH  "/app0/pms-payload.elf"
#define SHOP_URL      "http://127.0.0.1:8710/"

/* The two payload loaders a jailbroken PS4 might be running, in the two framings they use: the
   common one speaks HTTP and is what this console has, the other takes the bare ELF with no framing
   at all. Trying both costs one refused connect. (Naming them in a COMMENT is fine - the rule
   against naming software this project does not ship is about what a person reads on a screen.) */
#define LOADER_HTTP  9090   /* HTTP POST of the ELF */
#define LOADER_RAW   9021   /* raw ELF bytes */

/* The notification the console draws.
 *
 * THE ICON FIELD MUST STAY ZERO. `useIconImageUri = 1` selects the form that draws an icon beside
 * the text, and on this console family that form returns success and renders NOTHING - the project
 * has been caught by it twice on the PS5 (see the notification note in the project's own findings).
 * The payload in ps4-app/onconsole/server_ps4.c sends the plain form, measured working on this
 * exact console, so the tile sends the identical thing. A tile whose every message is invisible is
 * a tile that looks broken no matter what it actually did. */
static void notify(const char *text) {
    OrbisNotificationRequest req;
    memset(&req, 0, sizeof(req));
    snprintf(req.message, sizeof(req.message), "%s", text);
    sceKernelSendNotificationRequest(0, &req, sizeof(req), 0);
}

/* Is something listening on loopback? A refused connect settles immediately, so this is cheap. */
static int port_open(int port) {
    int s = socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) return 0;
    struct timeval tv;
    tv.tv_sec = 2; tv.tv_usec = 0;
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_port = htons((unsigned short)port);
    a.sin_addr.s_addr = inet_addr("127.0.0.1");
    int rc = connect(s, (struct sockaddr *)&a, sizeof(a));
    close(s);
    return rc == 0;
}

static int write_all(int fd, const char *buf, size_t len) {
    size_t off = 0;
    while (off < len) {
        ssize_t w = write(fd, buf + off, len - off);
        if (w <= 0) return -1;
        off += (size_t)w;
    }
    return 0;
}

/* Hand our payload to a loader, exactly the way the PC does it over the network. `http` picks the
   framing: one loader wants an HTTP POST, the other wants the bare ELF. Returns 0 if the
   loader took the whole thing.
 *
 * NEVER opens a connection it does not then fill with the payload. A loader that is handed an
 * empty connection can stop listening - which is how this console lost its loader twice while this
 * was being built, from nothing more than a port scan. That is also why there is no "is the loader
 * there?" probe: the connect below IS the probe, and it is always followed by the payload. */
static int send_payload_to_loader(int port, int http) {
    struct stat st;
    if (stat(PAYLOAD_PATH, &st) != 0 || st.st_size <= 0) return -1;

    int f = open(PAYLOAD_PATH, O_RDONLY);
    if (f < 0) return -1;

    int s = socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) { close(f); return -1; }
    struct timeval tv;
    tv.tv_sec = 30; tv.tv_usec = 0;
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_port = htons((unsigned short)port);
    a.sin_addr.s_addr = inet_addr("127.0.0.1");
    if (connect(s, (struct sockaddr *)&a, sizeof(a)) != 0) { close(s); close(f); return -1; }

    if (http) {
        char hdr[256];
        int hn = snprintf(hdr, sizeof(hdr),
                          "POST / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                          "Content-Type: application/octet-stream\r\n"
                          "Content-Length: %lld\r\nConnection: close\r\n\r\n",
                          port, (long long)st.st_size);
        if (write_all(s, hdr, (size_t)hn) != 0) { close(s); close(f); return -1; }
    }

    char *buf = (char *)malloc(64 * 1024);
    if (!buf) { close(s); close(f); return -1; }
    int bad = 0;
    for (;;) {
        ssize_t r = read(f, buf, 64 * 1024);
        if (r < 0) { bad = 1; break; }
        if (r == 0) break;
        if (write_all(s, buf, (size_t)r) != 0) { bad = 1; break; }
    }
    free(buf);
    close(f);
    /* Read whatever it answers so the loader is not left writing into a closed socket. */
    char rep[256];
    if (!bad) (void)read(s, rep, sizeof(rep));
    close(s);
    return bad ? -1 : 0;
}

/* Opening the browser has a prerequisite the shop's own payload does not: the user service.
 * The reference program for this on PS4 - the browser sample in the ps4-payload-dev SDK - calls
 * sceUserServiceInitialize first and only launches the browser if it succeeded, and this app is a
 * sandboxed application rather than a payload injected into a process that has already done it.
 * So do it here, once, and hand it back afterwards.
 *
 * FOUR ARGUMENTS, deliberately. The published PS4 declaration takes none (it is a stub), and the
 * PS5 build of this shop calls the same export with four, proven on hardware. Extra arguments in
 * registers are harmless on this ABI; passing too few would leave whatever was already in the
 * remaining registers to be read as parameters. Four is the form that is right either way. */
static void open_browser(const char *url) {
    int rc = sceUserServiceInitialize(0);
    printf("[PMS] sceUserServiceInitialize rc=0x%08x\n", rc);
    sceSystemServiceLaunchWebBrowser(url, 0, 0, 0);
    sceKernelUsleep(3 * 1000000);
    sceUserServiceTerminate();
}

/* Wait for the shop's socket after a loader accepted the payload. A cold start has to write the
   web files out before it listens, so this is seconds, not milliseconds. */
static int wait_for_shop(int seconds) {
    for (int i = 0; i < seconds; i++) {
        sceKernelUsleep(1000 * 1000);
        if (port_open(SHOP_PORT)) {
            printf("[PMS] the shop came up after %d second(s)\n", i + 1);
            return 1;
        }
    }
    return 0;
}

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    printf("[PMS] PKG MUTANT SHOP tile starting\n");

    if (port_open(SHOP_PORT)) {
        printf("[PMS] the shop is already running\n");
        notify("Opening PKG MUTANT SHOP");
        open_browser(SHOP_URL);
        return 0;
    }

    printf("[PMS] the shop is not running - looking for a payload loader\n");
    notify("Starting PKG MUTANT SHOP");

    int handed = 0;
    if (send_payload_to_loader(LOADER_HTTP, 1) == 0) {
        printf("[PMS] the loader on :%d took the payload\n", LOADER_HTTP);
        handed = 1;
    } else if (send_payload_to_loader(LOADER_RAW, 0) == 0) {
        printf("[PMS] the loader on :%d took the payload\n", LOADER_RAW);
        handed = 1;
    }

    if (handed) {
        if (wait_for_shop(30)) {
            notify("Opening PKG MUTANT SHOP");
            open_browser(SHOP_URL);
            return 0;
        }
        notify("PKG MUTANT SHOP could not start\n"
               "The payload was accepted but the shop never opened");
        printf("[PMS] payload accepted but :%d never opened\n", SHOP_PORT);
        return 1;
    }

    /* No loader. This is the one case a person has to act on, so it names the missing piece rather
       than failing silently - "run the jailbreak again" on its own sends people to re-run a
       jailbreak that is already running, which is what happened here. */
    notify("PKG MUTANT SHOP cannot start\n"
           "Nothing on this PS4 can load it right now - run the jailbreak on the console "
           "again, then open this app");
    printf("[PMS] no loader on :%d or :%d, and no shop on :%d\n",
           LOADER_HTTP, LOADER_RAW, SHOP_PORT);
    sceKernelUsleep(5 * 1000000);
    return 1;
}
