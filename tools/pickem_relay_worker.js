// Pick'em relay for SharpIQ's Cole's Plays lane (Nov 16, 2026). Free Cloudflare Worker.
//
// PrizePicks (and sometimes Underdog) refuse requests from GitHub's servers. This Worker fetches the same
// public line feeds from Cloudflare's network instead. It only talks to the two pick'em hosts below and
// only answers callers that send the secret key, so nobody else can use it.
//
// Setup (about 10 minutes, free):
//   1. dash.cloudflare.com -> sign up -> Workers & Pages -> Create -> "Hello World" Worker -> Deploy.
//   2. Edit code -> paste this whole file -> Deploy.
//   3. Settings -> Variables and Secrets -> add a Secret named RELAY_KEY (any long random string).
//   4. In GitHub (pm-edge -> Settings -> Secrets and variables -> Actions) add:
//        PICKEM_RELAY      = the Worker's URL, e.g. https://pickem-relay.<you>.workers.dev
//        PICKEM_RELAY_KEY  = the same string as RELAY_KEY
// Cole's Plays then uses the relay only when the direct request fails.

const ALLOWED = new Set([
  "api.prizepicks.com",
  "partner-api.prizepicks.com",
  "api.underdogfantasy.com",
]);

export default {
  async fetch(request, env) {
    if (!env.RELAY_KEY || request.headers.get("X-Relay-Key") !== env.RELAY_KEY) {
      return new Response("forbidden", { status: 403 });
    }
    const target = new URL(request.url).searchParams.get("url");
    let url;
    try {
      url = new URL(target);
    } catch {
      return new Response("bad url", { status: 400 });
    }
    if (url.protocol !== "https:" || !ALLOWED.has(url.hostname)) {
      return new Response("host not allowed", { status: 400 });
    }
    const pp = url.hostname.includes("prizepicks");
    const headers = {
      "User-Agent":
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      Accept: "application/json",
      "Accept-Language": "en-US,en;q=0.9",
    };
    if (pp) {
      headers.Referer = "https://app.prizepicks.com/";
      headers.Origin = "https://app.prizepicks.com";
    }
    const r = await fetch(url.toString(), { headers });
    return new Response(r.body, {
      status: r.status,
      headers: { "Content-Type": r.headers.get("Content-Type") || "application/json" },
    });
  },
};
