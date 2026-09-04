/* Helper payloads embedded via .incbin — the assembler links the raw bytes straight into the
   ELF, so there is no multi-megabyte C array to compile (a hex-array header for these two was
   33 MB of source). One ELF therefore ships the whole service. */
#ifndef PAYLOAD_BUNDLE_H
#define PAYLOAD_BUNDLE_H

#define PB_INCBIN(sym, file)                    \
  __asm__(".section .rodata\n"                  \
          ".global " #sym "\n"                  \
          ".balign 16\n"                        \
          #sym ":\n"                            \
          ".incbin \"" file "\"\n"              \
          ".global " #sym "_end\n"              \
          #sym "_end:\n"                        \
          ".previous\n");                       \
  extern const unsigned char sym[];             \
  extern const unsigned char sym##_end[];

PB_INCBIN(pb_shadowmount, "payloads/shadowmountplus.elf")
/* etaHEN IS NO LONGER BUNDLED (v3.33.0). We never started it - it was carried "so the user always
   has the build we tested against". They do not want it, we do not call it, and nothing in our lane
   reads it: pb_etahen appeared only in the table below, and the companion's host_payload_path()
   resolves through Payload Manager's own directory, not our copy, and only in the legacy "v2" mode.
   Carrying it cost 4.7 MB in this ELF and 4.7 MB written to the console on every boot, and its
   presence on disk was the only thing that made starting it by accident possible.
   The file is still in ps5-app/onconsole/payloads/ for reference; it is simply not embedded. */
/* OUR base-game installer. It exists because sceAppInstUtilInstallByPackage returns
   0x80B2116F from a payload INJECTED into a hijacked host (which the shop ELF is) and
   succeeds from a freshly SPAWNED process. Shipped, never auto-started - Payload Manager
   spawns it per install. See /api/engine/install-spawn. */
PB_INCBIN(pb_installer,   "payloads/pms-installer.elf")

/* `port` is the service the payload provides, and it doubles as the auto-start rule:
     >0  launch it only when that port is NOT already bound (relaunching a live install host is
         exactly what wedges installs), and treat a bound port as "already running".
      0  ship the file but never auto-start it.

   Neither Elf Arsenal nor etaHEN is bundled, and neither is the install host. Installs go through
   OUR spawned installer (pms-installer, below), which needs nothing to be running beforehand
   except Payload Manager. The history, kept because it explains the shape of this table:
   Arsenal and etaHEN both served DPI v2 on :12800 and could not coexist - whoever bound first
   kept it and the other retried forever, spewing "bind | Address already in use" notifications
   and a 2 MB log; Arsenal also dragged in nanodns, garlic and ftpsrv. etaHEN was then carried for
   a while at port 0, and briefly at 12800, which was a mistake: it patches the kernel/ShellCore,
   this console's own boot chain (pldmgr -> kstuff_lite -> shadowmountplus, per the user's
   autoload.txt) deliberately does not run it, and auto-starting it put a second kernel-touching
   daemon next to kstuff_lite that had never been part of a working boot - the first fake-signed
   PS4 title launched afterwards panicked the console. A payload that changes the jailbreak layer
   is the user's call to make, never a side effect of installing our app. Since 3.33.0 it is not
   embedded at all (see above), so there is no etaHEN entry in the table and nothing of ours can
   start it by accident. server.c's REST_STOP still names it, only to stand down a copy the user
   runs before rest mode. */
typedef struct { const char *name; const char *filename; int port;
                 const unsigned char *data; const unsigned char *end; } pb_entry_t;

static const pb_entry_t PAYLOAD_BUNDLE[] = {
  /* 10101, NOT 9021. ShadowMount announces its own listener in its log:
       [API] HTTP/JSON ready: http://127.0.0.1:10101/api/v1 (v1)
     9021 belongs to elfldr, the ELF loader, which is ALWAYS running. Because this number is
     also the "is it already up?" test, the check was permanently true and ShadowMount was
     therefore never actually auto-started by us - it only ever ran because the user
     autoloads it from Payload Manager. Same wrong port was reported as ShadowMount health. */
  { "shadowmount", "shadowmountplus.elf", 10101, pb_shadowmount, pb_shadowmount_end },
  { "pms-installer", "pms-installer.elf", 0,     pb_installer,   pb_installer_end   },
};
#define PAYLOAD_BUNDLE_COUNT 2

#endif
