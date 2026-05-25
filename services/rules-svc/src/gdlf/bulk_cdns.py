"""Global bulk-content CDN passthrough list.

These are hostname globs (fnmatch syntax) that mitmproxy passes through
untouched at the TLS layer — no decrypt/re-encrypt, no per-request decision
call, no activity-log entry. The single source of truth lives here in
rules-svc; the mitmproxy addon polls `/api/passthrough` and reads the
`globals` key, so updating this file + restarting rules-svc applies to
mitm within one refresh cycle (~30s).

Criteria for adding here:
  (1) Traffic is bulk-binary, already vendor-signed, and never something
      a parent would want to filter at URL level.
  (2) The hostname is narrow enough that we won't accidentally passthrough
      account / store-front / community-content endpoints under the same
      parent domain. Multi-tenant CDN umbrellas (*.akamaihd.net,
      *.cloudfront.net, *.fastly.net, *.llnwd.net) are deliberately NOT
      listed — match the specific vendor subdomain instead.

List largely cribbed from lancache's cache-domains (github.com/uklans),
Apple's enterprise networking doc (support.apple.com/en-us/101555), and
Adobe's enterprise endpoints list.
"""
from __future__ import annotations


# Grouped so the dashboard can render the list by vendor. Order matters
# only for display — matching is a flat fnmatch over the union.
BULK_CDN_GROUPS: dict[str, tuple[str, ...]] = {
    "Steam / Valve": (
        "*.steamcontent.com",
        "*.steampipe.steamcontent.com",
        "*.steamserver.net",
    ),
    "Microsoft / Windows / Office / Xbox": (
        "*.windowsupdate.com",
        "*.update.microsoft.com",
        "*.delivery.mp.microsoft.com",
        "*.dl.delivery.mp.microsoft.com",
        "*.do.dsp.mp.microsoft.com",
        "officecdn.microsoft.com",
        "officecdn-microsoft-com.akamaized.net",
        "*.assets1.xboxlive.com",
        "*.assets2.xboxlive.com",
        "xvcf1.xboxlive.com",
        "xvcf2.xboxlive.com",
        "d1.xboxlive.com",
    ),
    "Apple": (
        "swcdn.apple.com",
        "swdist.apple.com",
        "swdownload.apple.com",
        "mesu.apple.com",
        "osrecovery.apple.com",
        "oscdn.apple.com",
        "appldnld.apple.com",
        "updates.cdn-apple.com",
        "updates-http.cdn-apple.com",
        "xp.apple.com",
        "gg.apple.com",
        "gs.apple.com",
        "gdmf.apple.com",
        "osxapps.itunes.apple.com",
        "devimages-cdn.apple.com",
        "download.developer.apple.com",
        "*.mzstatic.com",
    ),
    # Deliberately narrow — *.googleusercontent.com is too broad and serves
    # user-visible content (Drive previews, YouTube avatars, etc.).
    "Google / Android": (
        "*.gvt1.com",
        "*.gvt2.com",
        "dl.google.com",
        "dl.l.google.com",
        "android.clients.google.com",
    ),
    "Adobe Creative Cloud": (
        "ccmdls.adobe.com",
        "ccmdl.adobe.com",
        "armdl.adobe.com",
        "cc-cdn.adobe.com",
    ),
    "Blizzard / Battle.net": (
        "*.cdn.blizzard.com",
        "blzddist*-a.akamaihd.net",
        "dist.blizzard.com",
        "level3.blizzard.com",
        "nydus.battle.net",
        "blizzard.vo.llnwd.net",
    ),
    "Epic Games / Unreal": (
        "*.epicgames.com",
        "*.unrealengine.com",
        "cloudflare.epicgamescdn.com",
        "epicgames-download1.akamaized.net",
        "fastly-download.epicgames.com",
        "egdownload.fastly-edge.com",
    ),
    "EA / Origin": (
        "lvlt.cdn.ea.com",
        "origin-a.akamaihd.net",
        "cdn-patch.swtor.com",
    ),
    "Ubisoft": (
        "*.cdn.ubi.com",
    ),
    "Riot": (
        "*.dyn.riotcdn.net",
        "l3cdn.riotgames.com",
        "worldwide.l3cdn.riotgames.com",
        "riotgamespatcher-a.akamaihd.net",
    ),
    "Rockstar": (
        "patches.rockstargames.com",
    ),
    "Sony / PlayStation": (
        "*.dl.playstation.net",
        "*.gs2.ww.prod.dl.playstation.net",
        "*.np.dl.playstation.net",
        "playstation4.sony.akadns.net",
    ),
    "Nintendo": (
        "*.hac.lp1.d4c.nintendo.net",
        "*.hac.lp1.eshop.nintendo.net",
        "*.wup.eshop.nintendo.net",
        "*.wup.shop.nintendo.net",
        "*.cdn.nintendo.net",
        "*.srv.nintendo.net",
    ),
    "Activision / Call of Duty": (
        "cod-assets.cdn.callofduty.com",
    ),
    "GOG": (
        "*.gog-cdn-fastly.gog.com",
        "gog-cdn-fastly.gog.com",
        "cdn.gog.com",
    ),
    "Nexus Mods": (
        "filedelivery.nexusmods.com",
    ),
    "Wargaming (WoT / WoWs / WoWp)": (
        "dl-wot-ak.wargaming.net",
        "dl-wot-cdx.wargaming.net",
        "dl-wot-gc.wargaming.net",
        "dl-wows-ak.wargaming.net",
        "dl-wows-cdx.wargaming.net",
        "dl-wows-gc.wargaming.net",
        "dl-wowp-ak.wargaming.net",
    ),
    "MMO patchers": (
        "patch-dl.ffxiv.com",
        "live.patcher.elderscrollsonline.com",
        "patchcdn.pathofexile.com",
        "content.warframe.com",
        "assetcdn.101.arenanetworks.com",
        "assetcdn.102.arenanetworks.com",
        "assetcdn.103.arenanetworks.com",
    ),
    # fast.com is Netflix's speed test; the actual probe pulls bulk video
    # chunks from Open Connect (*.nflxvideo.net). Listing both keeps
    # speed-test runs out of mitm.
    "Netflix / fast.com": (
        "fast.com",
        "*.fast.com",
        "*.nflxvideo.net",
    ),
    # --- Video streaming (segment CDNs only — the app domains stay in mitm
    # so URL rules can still flag opening the service). Deliberately
    # excluded: *.googlevideo.com (would break URL rules against YouTube
    # paths since SNI is the only signal mitm has for them), Peacock (no
    # dedicated segment subdomain). ---
    "Disney+": (
        "*.media.dssott.com",
        "disney.playback.edge.bamgrid.com",
        "star.playback.edge.bamgrid.com",
        "*.cnbl-cdn.bamgrid.com",
    ),
    "HBO Max / Max": (
        "*.hbomaxcdn.com",
        "*.hbomax-images.warnermediacdn.com",
    ),
    "Hulu": (
        "*.hulustream.com",
        "*.huluim.com",
    ),
    "Amazon Prime Video": (
        "*.aiv-delivery.net",
    ),
    "Apple TV+ / Music": (
        "*.tv.v.aaplimg.com",
        "*.hls-svod-aoc-ve.itunes.g.aaplimg.com",
        "*.tv.g.apple.com",
    ),
    "Paramount+": (
        "*.pplusstatic.com",
    ),
    "Twitch": (
        "*.ttvnw.net",
        "*.twitchcdn.net",
    ),
    "Spotify": (
        "*.audio.spotifycdn.com",
        "audio-ak-spotify-com.akamaized.net",
        "audio4-ak-spotify-com.akamaized.net",
        "*.scdn.co",
        "*.pscdn.co",
    ),
    # --- Software / dev distribution CDNs. Deliberately excluded: Discord
    # / Slack file CDNs (user-uploaded content worth keeping in mitm), npm
    # registry (tiny + many tools cert-pin it), OBS (ships via GitHub
    # releases which redirect through too-broad CDN parents). ---
    "Docker Hub": (
        "production.cloudflare.docker.com",
        "*.cloudflare.docker.com",
    ),
    "JetBrains": (
        "download-cdn.jetbrains.com",
        "download.jetbrains.com",
    ),
    "Zoom updates": (
        "cdn.zoom.us",
    ),
    # --- Speed tests beyond fast.com. ISP-specific Ookla mirrors live
    # under unbounded hosts; *.ooklaserver.net is what most installations
    # use. ---
    "Speed tests": (
        "*.speedtest.net",
        "*.ooklaserver.net",
        "speed.cloudflare.com",
    ),
}


# Flat tuple for fnmatch loops — assembled from the groups above.
BULK_CDN_PATTERNS: tuple[str, ...] = tuple(
    p for patterns in BULK_CDN_GROUPS.values() for p in patterns
)


# No global inspect defaults: splice-by-default for ALL traffic until the
# parent explicitly opts in a domain via `Kid.mitm_inspect_hosts`. This is
# the cleanest expression of the inversion — zero magic, every MITM target
# is a deliberate per-kid choice.
