// Record what harvest.js saw, and optionally walk the operator's own terms.
//
// Two modes, and the passive one is the default. Any Vine item page the
// operator loads themselves is recorded with no sweep running at all. A sweep
// is something they start by clicking the toolbar button, and it ends when the
// terms run out, when the search budget is spent, or when they click again.
// There is no timer that browses on its own.
//
// Every policy number this file obeys -- which terms, how far apart, how many
// searches, how many pages deep -- comes from the sink, which reads them from
// the repository configuration. The one number owned here is the backoff
// ladder, because it answers to Amazon's throttling rather than to anything
// an operator configured.
//
// Logs from this file appear in the service worker console (the "service
// worker" link on the extension's card). Logs from harvest.js appear in the
// Vine page's own console. They are different consoles.

// scripts/vine/sink.py owns this default. Change it in both places or neither.
const SINK = "http://127.0.0.1:8787";

const ITEMS_URL = SINK + "/items";
const PLAN_URL = SINK + "/plan";
const DUE_URL = SINK + "/due";
const SWEPT_URL = SINK + "/swept";

// The daily sweep. The sink decides whether one is owed, because it is the
// side that knows when the last one finished; this only asks, and only while
// Chrome happens to be running. Nothing here wakes a machine or opens a
// browser: a day with Chrome closed simply has no sweep, and the morning mail
// stays silent rather than reporting yesterday again.
//
// Checking is free of consequence -- /due does not move the rota, which is why
// it is a different route from /plan.
const DAILY_ALARM = "vine-daily";
const CHECK_EVERY_MINUTES = 30;

// Search was observed to return the same results whichever queue is named, so
// one queue covers the catalog. Checked 2026-10-06 against encore and
// last_chance for the same term; if that stops holding, this is the line that
// has to grow, not the record format.
const SWEEP_QUEUE = "encore";
const SEARCH_URL = "https://www.amazon.com/vine/vine-items";

// What Amazon's own throttling asks for: stop, wait longer each time, and give
// up the sweep rather than keep knocking once the waits get long. Chrome
// floors an alarm at 30 seconds, which is why the ladder starts there.
const BACKOFF_SECONDS = [30, 60, 120, 240, 480, 900];
const BACKOFF_ALARM = "vine-backoff";

// A sweep is driven entirely by messages from the page it opened, so a page
// that never sends one leaves it running with nothing to advance it -- and
// because a sweep is only started when none is active, that one silent page
// declines every sweep afterwards. Observed: the rota moved on, two terms of
// twelve were recorded, no finish was ever reported, and the companion did
// not sweep again until the extension was reloaded two days later.
//
// So the sweep carries a deadline for the answer it is waiting on, and the
// alarm that asks whether a sweep is owed ends one that has gone past it.
// This is a backstop, not the fix: harvest.js now answers an empty grid.
const PAGE_DEADLINE_SECONDS = 90;

const IDLE_STATE = {
  active: false,
  tabId: null,
  // Set when this sweep opened its own tab, so the scheduled run can clean up
  // after itself without ever closing a tab the operator was reading.
  ownTab: false,
  terms: [],
  index: 0,
  page: 1,
  searches: 0,
  maxSearches: 0,
  maxPages: 1,
  spacingSeconds: 0,
  backoffStep: 0,
  // When the answer this sweep is waiting for stops being merely late.
  quietUntil: 0,
  // Pages that reported while this sweep was running but did not come from
  // its own tab. Rows from one are still recorded -- they are real sightings
  // -- but they cannot move the sweep on, so a sweep can collect a page and
  // make no progress at all. That is invisible from here and was observed as
  // fourteen sweeps that recorded rows and none that ever reported an end, so
  // the count travels with the sweep's account of itself.
  strayReports: 0
};

async function readState() {
  const stored = await chrome.storage.session.get("sweep");
  return Object.assign({}, IDLE_STATE, stored.sweep || {});
}

async function writeState(state) {
  await chrome.storage.session.set({ sweep: state });
}

function setBadge(text, color) {
  chrome.action.setBadgeText({ text: text });
  chrome.action.setBadgeBackgroundColor({ color: color });
}

function setStatus(text) {
  chrome.action.setTitle({ title: "Auction Lens Vine companion\n" + text });
}

function sleep(seconds) {
  return new Promise(function (resolve) {
    setTimeout(resolve, seconds * 1000);
  });
}

function searchUrl(term, page) {
  const params = new URLSearchParams({ queue: SWEEP_QUEUE, search: term });
  if (page > 1) {
    params.set("page", String(page));
  }
  return SEARCH_URL + "?" + params.toString();
}

async function postItems(items, context, sweeping) {
  if (items.length === 0) {
    return;
  }
  try {
    const response = await fetch(ITEMS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: items, context: context })
    });
    if (!response.ok) {
      console.log("[vine] sink refused the batch:", response.status);
      setBadge("err", "#b00020");
      setStatus("Sink refused the last batch (" + response.status + ")");
      return;
    }
    const body = await response.json();
    console.log("[vine] recorded", body.recorded, "items to", body.file);
    setStatus("Recorded " + body.recorded + " items at " + new Date().toLocaleTimeString());
    // While sweeping, the badge counts terms left; there is nowhere else to
    // show progress. Browsing has no such counter, so it gets the batch size
    // -- otherwise there is no way to tell the companion is working without
    // opening a console.
    if (!sweeping) {
      setBadge(String(body.recorded), "#1a7f37");
    }
  } catch (error) {
    // The sink not running is the ordinary case, not a failure worth shouting
    // about: the operator may simply be browsing Vine with nothing collecting.
    console.log("[vine] sink unreachable; nothing recorded:", error.message);
    setBadge("off", "#6b6b6b");
    setStatus("Sink unreachable. Start scripts\\vine\\start-sink.cmd");
  }
}

async function reportSwept(searches, reason, strays) {
  // Every ending is reported, including one that searched nothing. The sink
  // decides what it means: how many searches were spent is what hands the
  // unused terms back to the rota, and whether the sweep spent its whole
  // window is what decides if the day is done. Staying silent about a sweep
  // that got nowhere threw away both facts -- the rota advanced past terms
  // nobody had searched, and the only account of why a night failed was a
  // line in a console no one reads at three in the morning.
  try {
    await fetch(SWEPT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        searches: searches,
        reason: reason,
        stray_reports: strays
      })
    });
  } catch (error) {
    // The records are already written; only the account of the sweep failed,
    // so the worst case is a repeated search rather than a lost one.
    console.log("[vine] could not report the sweep:", error.message);
  }
}

async function stopSweep(reason) {
  const state = await readState();
  await writeState(Object.assign({}, IDLE_STATE));
  await chrome.alarms.clear(BACKOFF_ALARM);
  await reportSwept(state.searches || 0, reason, state.strayReports || 0);
  if (state.ownTab && state.tabId !== null) {
    try {
      await chrome.tabs.remove(state.tabId);
    } catch (error) {
      console.log("[vine] the sweep's own tab was already gone");
    }
  }
  console.log("[vine] sweep ended:", reason);
  setBadge("", "#6b6b6b");
  setStatus("Sweep ended: " + reason);
}

async function visitCurrent(state) {
  const term = state.terms[state.index];
  // Written before the navigation, so a page that never answers is already
  // on the clock rather than waiting on a deadline that was never stored.
  state.quietUntil = Date.now() + PAGE_DEADLINE_SECONDS * 1000;
  await writeState(state);
  setBadge(String(state.terms.length - state.index), "#1f6feb");
  const where = state.page > 1 ? term + " page " + state.page : term;
  setStatus("Searching " + where);
  console.log("[vine] searching", where);
  await chrome.tabs.update(state.tabId, { url: searchUrl(term, state.page) });
}

async function startSweep() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs[0];
  if (!tab || !tab.url || tab.url.indexOf(SEARCH_URL) !== 0) {
    // Navigating a tab the operator was reading would be a surprise. Say no
    // instead, and let them open Vine themselves.
    console.log("[vine] open a Vine item page in this tab first");
    setBadge("?", "#b58900");
    setStatus("Open a Vine item page in this tab, then click again");
    return;
  }
  await beginSweep(tab.id, false);
}

async function startScheduledSweep() {
  // Its own background tab, opened blank so the state is written before any
  // page can load and report into it, and closed again when the sweep ends.
  // The operator's tabs are left exactly as they were.
  const tab = await chrome.tabs.create({ url: "about:blank", active: false });
  const started = await beginSweep(tab.id, true);
  if (!started) {
    try {
      await chrome.tabs.remove(tab.id);
    } catch (error) {
      console.log("[vine] the unused tab was already gone");
    }
  }
}

async function beginSweep(tabId, ownTab) {
  let plan;
  try {
    const response = await fetch(PLAN_URL);
    plan = await response.json();
  } catch (error) {
    console.log("[vine] no plan; is scripts/vine/sink.py running?", error.message);
    setBadge("off", "#6b6b6b");
    setStatus("No plan. Start scripts\\vine\\start-sink.cmd");
    return false;
  }
  if (!plan.terms || plan.terms.length === 0) {
    console.log("[vine] the plan names no terms");
    setBadge("?", "#b58900");
    setStatus("The plan names no terms");
    return false;
  }
  // Built from IDLE_STATE rather than written out again, so every field a
  // sweep carries exists from the first write. Listing them here instead
  // silently dropped two: `visitId` went unset, `visitCurrent` incremented
  // undefined to NaN, and the test for "this page already moved the sweep
  // on" compared NaN with NaN, which is false, so it never once held.
  const state = Object.assign({}, IDLE_STATE, {
    active: true,
    tabId: tabId,
    ownTab: ownTab,
    terms: plan.terms,
    maxSearches: plan.max_searches,
    maxPages: plan.max_pages_per_term || 1,
    spacingSeconds: plan.spacing_seconds
  });
  await writeState(state);
  console.log(
    "[vine] sweeping",
    plan.terms.length,
    "terms",
    plan.spacing_seconds + "s apart,",
    state.maxPages,
    "page(s) each"
  );
  await visitCurrent(state);
  return true;
}

async function continueSweep(state, context) {
  // The answer has to be about the page this sweep is waiting on. Amazon
  // rewrites a grid in place rather than reloading it, so harvest.js can
  // report one page twice, and by the time the second report arrives the
  // sweep has already navigated -- so counting visits cannot tell that
  // report apart from the next page's first one, and acting on both skips a
  // term without searching it. The page states which search and which page
  // number it read, and that is the only fact here that identifies it.
  if (context.search_term !== state.terms[state.index] || context.page !== state.page) {
    return;
  }

  // Every page visited is a request, so paging spends the same budget a term
  // does. Counting them together is what keeps a deep term from quietly
  // turning a twelve-search sweep into fifty.
  state.searches = state.searches + 1;
  state.backoffStep = 0;

  const depth = Math.min(context.pages_available || 1, state.maxPages);
  if (context.page < depth) {
    state.page = context.page + 1;
  } else {
    state.index = state.index + 1;
    state.page = 1;
  }

  // Stored before either way out, because stopSweep reads the count back from
  // storage to report it. Writing only on the continuing path lost the last
  // search of every sweep -- and a sweep allowed one search reported none,
  // which reportSwept reads as "not a sweep" and declines to record at all.
  await writeState(state);
  if (state.index >= state.terms.length) {
    await stopSweep("every term searched");
    return;
  }
  if (state.searches >= state.maxSearches) {
    await stopSweep("search budget spent");
    return;
  }
  // Awaiting here is what keeps this worker alive across the gap. The spacing
  // is seconds, well inside the idle window, so it does not need an alarm --
  // and an alarm could not express it, since Chrome floors those at 30s.
  await sleep(state.spacingSeconds);
  const current = await readState();
  if (!current.active) {
    return;
  }
  await visitCurrent(current);
}

async function backOff(state) {
  const seconds = BACKOFF_SECONDS[state.backoffStep];
  if (seconds === undefined) {
    await stopSweep("throttled past the longest wait");
    return;
  }
  state.backoffStep = state.backoffStep + 1;
  // A sweep waiting out a throttle is not stalled, so its deadline covers the
  // wait it actually asked for as well as the answer that follows.
  state.quietUntil = Date.now() + (seconds + PAGE_DEADLINE_SECONDS) * 1000;
  await writeState(state);
  console.log("[vine] throttled; waiting", seconds, "seconds");
  setBadge("wait", "#b58900");
  setStatus("Throttled. Waiting " + seconds + "s, then retrying the same term");
  chrome.alarms.create(BACKOFF_ALARM, { delayInMinutes: seconds / 60 });
}

async function handleMessage(message, sender) {
  const state = await readState();
  const fromSweptTab = state.active && sender.tab && sender.tab.id === state.tabId;

  if (message.type === "tiles") {
    await postItems(message.items, message.context, state.active);
    if (fromSweptTab) {
      await continueSweep(state, message.context);
      return;
    }
    if (state.active) {
      // Either the operator is browsing Vine while a sweep runs, which is
      // ordinary, or the sweep's own tab is no longer the tab it thinks it
      // opened, which is not. Counting them is what tells those apart in the
      // morning without a console.
      state.strayReports = (state.strayReports || 0) + 1;
      await writeState(state);
    }
    return;
  }
  if (message.type === "blocked") {
    console.log("[vine] no item grid on", message.url);
    if (fromSweptTab) {
      await backOff(state);
    } else {
      setStatus("No item grid on that page; nothing recorded");
    }
  }
}

chrome.runtime.onMessage.addListener(function (message, sender) {
  // Returning true holds the message port open, which is also what keeps this
  // worker from being evicted while the handler is still waiting.
  handleMessage(message, sender);
  return true;
});

chrome.action.onClicked.addListener(async function () {
  const state = await readState();
  if (state.active) {
    await stopSweep("stopped by the operator");
    return;
  }
  await startSweep();
});

async function checkWhetherOwed() {
  const state = await readState();
  if (state.active) {
    return;
  }
  let answer;
  try {
    const response = await fetch(DUE_URL);
    answer = await response.json();
  } catch (error) {
    // The sink not running is the ordinary case for a machine that is simply
    // being used for something else. Not worth a badge.
    return;
  }
  if (!answer.sweep_due) {
    return;
  }
  console.log("[vine] a sweep is owed today; starting one");
  await startScheduledSweep();
}

async function endIfStalled() {
  const state = await readState();
  if (!state.active || !state.quietUntil) {
    return;
  }
  if (Date.now() <= state.quietUntil) {
    return;
  }
  // Ending it reports what it did search, closes the tab it opened, and lets
  // the next check start a fresh one. Whether today still counts as swept is
  // reportSwept's existing rule, not a new one: a sweep that searched nothing
  // claims nothing.
  await stopSweep("the page stopped answering");
}

chrome.alarms.onAlarm.addListener(async function (alarm) {
  if (alarm.name === DAILY_ALARM) {
    await endIfStalled();
    await checkWhetherOwed();
    return;
  }
  if (alarm.name !== BACKOFF_ALARM) {
    return;
  }
  const state = await readState();
  if (!state.active) {
    return;
  }
  // Retry the same term and page the throttle interrupted rather than skipping.
  await visitCurrent(state);
});

function scheduleDailyCheck() {
  // Chrome keeps alarms across restarts, so creating it with the same name is
  // a replace rather than a duplicate.
  chrome.alarms.create(DAILY_ALARM, {
    delayInMinutes: 1,
    periodInMinutes: CHECK_EVERY_MINUTES
  });
}

chrome.runtime.onInstalled.addListener(scheduleDailyCheck);
chrome.runtime.onStartup.addListener(scheduleDailyCheck);
