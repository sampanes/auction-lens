// Drive the companion's two halves against a stubbed page and a stubbed sink.
//
// The extension is the only part of this project that cannot be reached from
// the Python suite, and it is the part that decides whether any Vine record
// exists at all. A sweep advances only because the page it opened sent a
// message, so the failures worth testing are not wrong values but silence:
// a page that says nothing leaves the sweep running with nothing to drive it,
// and a sweep that is still running declines the next one.
//
// Run it directly, or let scripts/check.py run it:
//
//     node scripts/vine/test-extension.js
//
// It needs Node and nothing else. There is no package.json, no lockfile and
// no installed package here, so nothing a registry publishes can change what
// this gate does; `vm`, `fs` and `path` are Node's own. Nothing here opens a
// socket, writes a file, or reads anything outside this directory.
//
// What it models: one page load per navigation, the grid that page rendered,
// the messages the two halves exchange, and the clock. What it does not
// model: Amazon's markup, Chrome's service worker eviction, and real timing.
// So it answers "does this control flow reach the next term", which is the
// question the bugs were in, and not "does the selector still match".

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const EXTENSION = path.join(__dirname, "extension");

// Invented throughout. A fixture that names a real search term or a recorded
// product would put the operator's own criteria into public history.
//
// The identifiers are ten characters because that is what tileAsin captures.
// Eleven-character ones were all truncated to the same ten, so tiles meant to
// differ compared equal and harvest.js deduplicated a page against itself.
const PLAN = {
  terms: ["sundial", "birdhouse", "trivet"],
  max_searches: 3,
  max_pages_per_term: 1,
  // Zero so the suite does not wait out the real spacing. The gap itself is
  // the sink's to choose and is tested where it is read, not here.
  spacing_seconds: 0
};

function tile(asin, title) {
  return {
    querySelector(selector) {
      if (selector.includes("/dp/")) {
        return { getAttribute: () => "/dp/" + asin };
      }
      if (selector === ".a-truncate-full") {
        return { textContent: title };
      }
      if (selector.startsWith("img")) {
        return { getAttribute: () => "https://example.invalid/" + asin + ".jpg" };
      }
      return null;
    }
  };
}

const SUNDIAL = tile("B0SYNTH001", "Acme Sundial, Brass");
const BIRDHOUSE = tile("B0SYNTH002", "Acme Birdhouse, Cedar");
const TRIVET = tile("B0SYNTH003", "Acme Trivet, Two Pack");

// One page load, as Chrome injects harvest.js afresh on every navigation.
function loadPage(url, tiles, hasGrid, sendMessage) {
  const timers = [];
  const watchers = [];
  const grid = hasGrid ? { children: tiles } : null;
  const address = new URL(url);
  const document = {
    documentElement: {},
    // Only what decodeEntities needs: innerHTML in, text out.
    createElement: () => ({
      innerHTML: "",
      get value() {
        return this.innerHTML;
      }
    }),
    querySelector: (selector) => (selector === "#vvp-items-grid" ? grid : null)
  };
  const context = {
    document: document,
    window: {
      location: { search: address.search, pathname: address.pathname },
      setTimeout: (fn) => timers.push(fn),
      clearTimeout: () => {}
    },
    MutationObserver: class {
      constructor(fn) {
        this.fn = fn;
        watchers.push(this);
      }
      observe() {}
      disconnect() {
        this.stopped = true;
      }
    },
    URL: URL,
    URLSearchParams: URLSearchParams,
    Number: Number,
    Array: Array,
    Date: Date,
    chrome: { runtime: { sendMessage: sendMessage } },
    console: { log: () => {} }
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(EXTENSION, "harvest.js"), "utf8"), context);
  return {
    // Jump to the grid deadline, which is where an empty page answers.
    fire: () => timers.splice(0).forEach((fn) => fn()),
    // Amazon rewriting a grid in place after the page already answered.
    rewrite: (replacement) => {
      grid.children = replacement;
      watchers.filter((w) => !w.stopped).forEach((w) => w.fn());
    }
  };
}

function loadWorker() {
  const store = {};
  const log = [];
  const visited = [];
  const alarms = {};
  const posted = [];
  const tabs = { opened: 0, closed: [] };
  const listeners = {};
  let finished = null;

  const context = {
    chrome: {
      storage: {
        session: {
          // Cloned both ways, as chrome.storage.session does. A stub that
          // handed back the live object would let a field the worker never
          // wrote appear to be there, which is the bug this suite exists for.
          get: async (key) =>
            store[key] === undefined ? {} : { [key]: structuredClone(store[key]) },
          set: async (values) => {
            for (const key of Object.keys(values)) {
              store[key] = structuredClone(values[key]);
            }
          }
        }
      },
      action: {
        setBadgeText: () => {},
        setBadgeBackgroundColor: () => {},
        setTitle: () => {},
        onClicked: { addListener: (fn) => { listeners.clicked = fn; } }
      },
      alarms: {
        create: (name, options) => { alarms[name] = options; },
        clear: async (name) => { delete alarms[name]; },
        onAlarm: { addListener: (fn) => { listeners.alarm = fn; } }
      },
      tabs: {
        query: async () => [{ id: 1, url: "https://www.amazon.com/vine/vine-items" }],
        create: async () => {
          tabs.opened += 1;
          return { id: 9 };
        },
        remove: async (id) => { tabs.closed.push(id); },
        update: async (id, options) => { visited.push(options.url); }
      },
      runtime: {
        onMessage: { addListener: (fn) => { listeners.message = fn; } },
        onInstalled: { addListener: () => {} },
        onStartup: { addListener: () => {} }
      }
    },
    // The sink, reduced to the four answers the extension asks it for.
    fetch: async (url, options) => {
      if (url.endsWith("/due")) {
        return { ok: true, json: async () => ({ sweep_due: true }) };
      }
      if (url.endsWith("/plan")) {
        return { ok: true, json: async () => PLAN };
      }
      if (url.endsWith("/items")) {
        posted.push(JSON.parse(options.body).items.length);
        return { ok: true, json: async () => ({ recorded: 1, file: "records.ndjson" }) };
      }
      if (url.endsWith("/swept")) {
        finished = JSON.parse(options.body);
        return { ok: true, json: async () => ({ date: "2026-01-01" }) };
      }
      throw new Error("the extension asked for something unexpected: " + url);
    },
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    structuredClone: structuredClone,
    URLSearchParams: URLSearchParams,
    Date: Date,
    Math: Math,
    JSON: JSON,
    Object: Object,
    String: String,
    Number: Number,
    console: { log: (...parts) => log.push(parts.join(" ")) }
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(EXTENSION, "sweep.js"), "utf8"), context);

  // Long enough for the handlers' own awaits to run out, which is all the
  // ordering this needs: the spacing above is zero.
  const settle = () => new Promise((resolve) => setTimeout(resolve, 60));
  return {
    log: log,
    visited: visited,
    alarms: alarms,
    posted: posted,
    tabs: tabs,
    settle: settle,
    finished: () => finished,
    state: () => store.sweep || {},
    forceState: (patch) => { store.sweep = Object.assign({}, store.sweep, patch); },
    wake: async () => {
      await listeners.alarm({ name: "vine-daily" });
      await settle();
    },
    say: async (message) => {
      await listeners.message(message, { tab: { id: 9 } });
      await settle();
    }
  };
}

// Start the scheduled sweep and hand it the pages it asks for, in order. A
// page is only loaded because the sweep navigated to it; if it did not, the
// sweep is stuck, and `stalled` records that rather than feeding it the same
// URL again and calling the result progress.
async function sweepOver(pages) {
  const worker = loadWorker();
  await worker.wake();
  let served = 0;
  for (const page of pages) {
    if (worker.visited.length <= served) {
      worker.stalled = true;
      break;
    }
    const dom = loadPage(
      worker.visited[served],
      page.tiles || [],
      page.hasGrid !== false,
      (message) => worker.say(message)
    );
    served += 1;
    dom.fire();
    await worker.settle();
    if (page.rewrittenTo) {
      dom.rewrite(page.rewrittenTo);
      dom.fire();
      await worker.settle();
    }
  }
  await worker.settle();
  return worker;
}

const CASES = [
  [
    "a term with no results costs a term, not the sweep",
    // The whole feature turned on this. A search with no results renders the
    // grid and never mutates it, harvest.js returned silently, and the sweep
    // waited for a message that was not coming -- which also declined every
    // later sweep, because one is started only when none is running.
    async (check) => {
      const worker = await sweepOver([{ tiles: [] }, { tiles: [SUNDIAL] }]);
      check("the sweep kept going", !worker.stalled, true);
      check("it reached the second term", /search=birdhouse/.test(worker.visited[1]), true);
      check("and recorded nothing for the empty page", worker.posted, [1]);
    }
  ],
  [
    "a sweep that finds nothing at all still finishes",
    async (check) => {
      const worker = await sweepOver([{ tiles: [] }, { tiles: [] }, { tiles: [] }]);
      check("it is no longer running", worker.state().active, false);
      check("it reported itself finished", (worker.finished() || {}).searches, 3);
      check("and closed the tab it opened", worker.tabs.closed, [9]);
    }
  ],
  [
    "a productive sweep records every term and counts every search",
    // The count is what the sink stores, and a sweep allowed one search used
    // to report none -- which the extension reads as "not a sweep" and does
    // not record, so the morning mail had nothing to find.
    async (check) => {
      const worker = await sweepOver([
        { tiles: [SUNDIAL] },
        { tiles: [BIRDHOUSE] },
        { tiles: [TRIVET] }
      ]);
      check("every term posted its batch", worker.posted, [1, 1, 1]);
      check("it ended on its own", worker.state().active, false);
      check("the last search was counted", (worker.finished() || {}).searches, 3);
    }
  ],
  [
    "no grid at all is a throttle, and is waited out",
    // An empty grid and a missing grid are different facts. One is a term
    // that was searched; the other is a 429, an interstitial or a sign-in
    // wall, and only it earns the backoff ladder.
    async (check) => {
      const worker = await sweepOver([{ hasGrid: false }]);
      check("a backoff was scheduled", "vine-backoff" in worker.alarms, true);
      check("and the term was not given up on", worker.visited.length, 1);
    }
  ],
  [
    "a page that answers twice does not skip a term",
    // Amazon rewrites a grid in place rather than reloading it. Advancing on
    // the second report would pass over the next term without searching it,
    // which is indistinguishable afterwards from a term that found nothing.
    async (check) => {
      const worker = await sweepOver([{ tiles: [], rewrittenTo: [SUNDIAL] }]);
      check("the sweep moved on exactly once", worker.visited.length, 2);
    }
  ],
  [
    "a page that reports twice with tiles both times still costs one term",
    // The case above is caught by harvest.js, which will not speak again
    // after it has answered an empty grid. A page that had tiles and is then
    // rewritten with different ones has no such guard on the page side: it
    // reports both, and only the worker can know that it already acted on
    // this navigation. That makes this the test for the worker's own guard,
    // and the one above the test for the page's.
    async (check) => {
      const worker = await sweepOver([{ tiles: [SUNDIAL], rewrittenTo: [TRIVET] }]);
      check("the sweep moved on exactly once", worker.visited.length, 2);
      check("and counted one search, not two", worker.state().searches, 1);
    }
  ],
  [
    "a page that never answers ends the sweep instead of jamming it",
    // Nothing else can end a sweep: it is driven by the page. Before this,
    // one silent page left the companion marked busy until the browser was
    // restarted, and every sweep in between was declined.
    async (check) => {
      const worker = loadWorker();
      await worker.wake();
      check("the sweep is waiting with a deadline", worker.state().quietUntil > 0, true);

      worker.forceState({ quietUntil: Date.now() - 1000 });
      await worker.wake();
      check(
        "the stall is named in the log",
        worker.log.some((line) => line.includes("stopped answering")),
        true
      );
      // The sink is the only thing that keeps a record overnight, so the
      // reason has to reach it rather than stop at the worker's console.
      check("the sink was told why it ended",
        (worker.finished() || {}).reason, "the page stopped answering");
      check("  and that nothing was searched",
        (worker.finished() || {}).searches, 0);
      check("the tab it opened was closed", worker.tabs.closed, [9]);
      // It searched nothing, so the day is not claimed and the same check
      // starts a fresh sweep rather than losing the evening.
      check("a fresh sweep took its place", worker.tabs.opened, 2);
    }
  ]
];

(async () => {
  let failed = 0;
  for (const [name, run] of CASES) {
    const failures = [];
    const check = (what, got, want) => {
      try {
        assert.deepStrictEqual(got, want);
      } catch (error) {
        failures.push(what + ": got " + JSON.stringify(got) + ", wanted " + JSON.stringify(want));
      }
    };
    try {
      await run(check);
    } catch (error) {
      failures.push("threw " + error.message);
    }
    if (failures.length === 0) {
      console.log("[OK] " + name);
      continue;
    }
    failed += 1;
    console.log("[X]  " + name);
    for (const failure of failures) {
      console.log("       " + failure);
    }
  }
  console.log("");
  if (failed) {
    console.log("[X] " + failed + " of " + CASES.length + " companion checks failed");
    process.exit(1);
  }
  console.log("[OK] all " + CASES.length + " companion checks passed");
})();
