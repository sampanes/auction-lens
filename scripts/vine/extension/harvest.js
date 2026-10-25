// Read the Vine item grid that is already on screen and report it.
//
// It runs when the operator loads a Vine item page -- whether they navigated
// there themselves or the sweep did -- reads the tiles the page already
// rendered, and hands them to sweep.js. Its one request of its own is the
// value lookup below, made only when the sweep asks for it.
//
// Observed page contract, checked 2026-10-06 against a live signed-in queue:
//   queue is one of potluck (recommended), last_chance (available for all),
//   encore (additional items); &search=TERM filters and ignores the queue;
//   &page=N pages 36 tiles; &pn=NODE narrows to a category. A tile carries
//   its ASIN in a /dp/ link and its full title in the offscreen half of
//   Amazon's truncation widget. No tile carries an ETV.
//
// The ETV, checked 2026-10-22: a tile's details button carries
// data-recommendation-id, data-asin and data-is-parent-asin. The details
// modal reads GET /vine/api/recommendations/{id}/item/{asin}, whose result
// holds taxValue and taxCurrency. A parent ASIN's item is one of its
// variations: GET /vine/api/recommendations/{id} lists them, and the first
// one's value is the one recorded. Both are reads. Nothing here clicks
// anything, and the request button is never touched -- its endpoint is not
// named anywhere in this extension.

const GRID_SELECTOR = "#vvp-items-grid";
const PAGER_SELECTOR = ".a-pagination";
const ASIN_PATTERN = /\/dp\/([A-Z0-9]{10})/;

// Amazon's truncation widget renders the full string offscreen and a clipped
// copy for display. The full one is what a matching rule should see.
const FULL_TITLE_SELECTOR = ".a-truncate-full";

// How long a real queue may take to render before this page is treated as
// something other than a queue. Reporting "blocked" too eagerly would cost a
// sweep a multi-minute backoff for a page that was merely still drawing.
const GRID_TIMEOUT_MS = 8000;

// Grid changes arrive as several mutations; wait for them to stop before
// reading, or one page produces several partial batches.
const SETTLE_MS = 400;

// The sweep advances on each message from this page, so every page it opens
// has to send exactly one answer. An empty grid is an answer: a search with no
// results renders the grid and then never mutates it. The first version of
// this file returned silently on that, which stopped the sweep dead on its
// first fruitless term and left it marked active -- and a sweep marked active
// declines every later one, so the companion searched two terms of twelve and
// then nothing at all until the browser restarted.
let harvested = false;
let answeredEmpty = false;
let foundGrid = null;
let lastSignature = "";
let settleTimer = null;

// Amazon leaves HTML entities in the full-title element as literal text, so a
// 54 inch deck belt arrives as 54&#34; and a left &amp; right bracket keeps
// its escape. A rule hunting a dimension would miss exactly the titles that
// state one, which is the opposite of what a dimension rule is for. Decode to
// what a person actually reads on the page before anything matches it.
function decodeEntities(text) {
  const holder = document.createElement("textarea");
  holder.innerHTML = text;
  return holder.value;
}

function tileAsin(tile) {
  const link = tile.querySelector('a[href*="/dp/"]');
  if (!link) {
    return "";
  }
  const found = ASIN_PATTERN.exec(link.getAttribute("href") || "");
  return found ? found[1] : "";
}

function tileTitle(tile) {
  const full = tile.querySelector(FULL_TITLE_SELECTOR);
  if (full && full.textContent.trim()) {
    return decodeEntities(full.textContent.trim());
  }
  // Older tiles put the whole title in the image's alt text; a truncated
  // title would quietly weaken every rule, so this is worth the fallback.
  const image = tile.querySelector("img[alt]");
  return image ? decodeEntities(image.getAttribute("alt").trim()) : "";
}

function tileImage(tile) {
  const image = tile.querySelector("img[src]");
  return image ? image.getAttribute("src") : "";
}

// The bare item page names no queue in its URL, yet one is always selected on
// screen. Reading the selected control keeps those rows attributed to a queue
// instead of landing blank.
function selectedQueue() {
  const chosen = document.querySelector('[aria-checked="true"][href*="queue="]');
  if (!chosen) {
    return "";
  }
  const found = /queue=([a-z_]+)/.exec(chosen.getAttribute("href") || "");
  return found ? found[1] : "";
}

// The highest page the pager offered. Amazon shows a window of page numbers
// rather than all of them, so on a long result set this understates the real
// total; it is a floor on what exists, not a count of it.
function pagesAvailable() {
  const pager = document.querySelector(PAGER_SELECTOR);
  if (!pager) {
    return 1;
  }
  const numbers = Array.from(pager.querySelectorAll("a, li"))
    .map(function (node) {
      return Number(node.textContent.trim());
    })
    .filter(function (value) {
      return Number.isFinite(value) && value > 0;
    });
  return numbers.length ? Math.max.apply(null, numbers) : 1;
}

function pageContext() {
  const params = new URLSearchParams(window.location.search);
  return {
    queue: params.get("queue") || selectedQueue(),
    search_term: params.get("search") || "",
    category_node: params.get("pn") || "",
    page: Number(params.get("page") || 1),
    pages_available: pagesAvailable()
  };
}

function readTiles(grid) {
  const context = pageContext();
  const seenAt = new Date().toISOString();
  const items = [];
  for (const tile of grid.children) {
    const asin = tileAsin(tile);
    const title = tileTitle(tile);
    if (!asin || !title) {
      continue;
    }
    items.push({
      source: "vine",
      asin: asin,
      title: title,
      image_url: tileImage(tile),
      queue: context.queue,
      search_term: context.search_term,
      category_node: context.category_node,
      page: context.page,
      pages_available: context.pages_available,
      seen_at: seenAt,
      // Deliberately absent from the grid. Recording the gap keeps a later
      // enrichment step honest about which rows it has already answered.
      etv: null
    });
  }
  return { items: items, context: context };
}

function report(grid, allowEmpty) {
  // An empty answer is final. A batch arriving after one would advance the
  // sweep a second time for the same page, skipping the next term.
  if (answeredEmpty) {
    return;
  }
  const reading = readTiles(grid);
  if (reading.items.length === 0 && !allowEmpty) {
    // Most likely still drawing. waitForGrid's deadline is what decides when
    // empty has stopped being "not yet" and become the answer.
    return;
  }
  // A grid can be rewritten in place rather than reloaded. Sending only when
  // the set of items actually changed keeps a redraw from recording twice.
  const signature = reading.items
    .map(function (item) {
      return item.asin;
    })
    .join(",");
  if (harvested && signature === lastSignature) {
    return;
  }
  lastSignature = signature;
  harvested = true;
  answeredEmpty = reading.items.length === 0;
  chrome.runtime.sendMessage({
    type: "tiles",
    items: reading.items,
    context: reading.context
  });
}

function scheduleReport(grid) {
  window.clearTimeout(settleTimer);
  settleTimer = window.setTimeout(function () {
    report(grid);
  }, SETTLE_MS);
}

function watchGrid(grid) {
  // Kept so the deadline can read a grid that rendered nothing.
  foundGrid = grid;
  // One mechanism covers two cases: tiles that finish rendering after this
  // script runs, and a grid Amazon replaces in place on a later page.
  new MutationObserver(function () {
    scheduleReport(grid);
  }).observe(grid, { childList: true });
  report(grid);
}

function waitForGrid() {
  const existing = document.querySelector(GRID_SELECTOR);
  let observer = null;
  if (existing) {
    watchGrid(existing);
  } else {
    observer = new MutationObserver(function () {
      const grid = document.querySelector(GRID_SELECTOR);
      if (grid) {
        observer.disconnect();
        watchGrid(grid);
      }
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }
  // One deadline covers both ways a page can fail to say anything, and the
  // two are not the same answer: a grid holding no tiles is a term that was
  // searched and found nothing, while no grid at all is a throttle, an
  // interstitial or a sign-in wall. Only the second deserves a backoff, so
  // this has to tell them apart rather than report whichever is easier.
  window.setTimeout(function () {
    if (observer) {
      observer.disconnect();
    }
    if (harvested) {
      return;
    }
    if (foundGrid) {
      report(foundGrid, true);
      return;
    }
    // sweep.js decides how long to wait; it is the only place that knows the
    // backoff ladder.
    chrome.runtime.sendMessage({ type: "blocked", url: window.location.pathname });
  }, GRID_TIMEOUT_MS);
}

// Every path this file requests starts here, and nothing else is built.
const DETAILS_PATH = "/vine/api/recommendations/";
const DETAILS_BUTTON = "input[data-recommendation-id]";

// The modal is opened by a person, one at a time. Two seconds between lookups
// keeps a handful of them from arriving as a burst.
const VALUE_SPACING_MS = 2000;

function pause(ms) {
  return new Promise(function (resolve) {
    window.setTimeout(resolve, ms);
  });
}

async function readDetails(path) {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    credentials: "same-origin"
  });
  if (!response.ok) {
    return null;
  }
  const body = await response.json();
  return body && body.result ? body.result : null;
}

async function valueOf(button) {
  const base = DETAILS_PATH + encodeURIComponent(button.dataset.recommendationId);
  let asin = button.dataset.asin;
  let variants = 0;
  if (button.dataset.isParentAsin === "true") {
    const parent = await readDetails(base);
    const variations = parent && parent.variations;
    if (!variations || !variations.length) {
      return null;
    }
    variants = variations.length;
    asin = variations[0].asin;
    await pause(VALUE_SPACING_MS);
  }
  const item = await readDetails(base + "/item/" + encodeURIComponent(asin));
  if (!item || typeof item.taxValue !== "number") {
    return null;
  }
  return {
    etv: item.taxValue,
    etv_currency: item.taxCurrency || "",
    // A parent's variations can carry different values. Recording how many
    // there were says the value is one of several rather than the only one.
    etv_variants: variants
  };
}

// The items asked for, read again from the grid with their values filled in.
// Rebuilt from the tile rather than sent as a bare number, so each one is a
// complete record and the newest sighting of an item is always a whole row.
async function lookUpValues(asins) {
  if (!foundGrid) {
    return [];
  }
  const tiles = Array.from(foundGrid.children);
  const items = readTiles(foundGrid).items;
  const valued = [];
  let asked = 0;
  for (const item of items) {
    if (asins.indexOf(item.asin) === -1) {
      continue;
    }
    const tile = tiles.find(function (candidate) {
      return tileAsin(candidate) === item.asin;
    });
    const button = tile && tile.querySelector(DETAILS_BUTTON);
    if (!button) {
      continue;
    }
    if (asked > 0) {
      await pause(VALUE_SPACING_MS);
    }
    asked = asked + 1;
    try {
      const value = await valueOf(button);
      if (value) {
        valued.push(Object.assign({}, item, value, { seen_at: new Date().toISOString() }));
      }
    } catch (error) {
      console.log("[vine] could not read the value of", item.asin, error.message);
    }
  }
  return valued;
}

chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (!message || message.type !== "values") {
    return false;
  }
  lookUpValues(message.asins || []).then(
    function (items) {
      sendResponse({ items: items });
    },
    function (error) {
      sendResponse({ items: [], error: error.message });
    }
  );
  return true;
});

waitForGrid();
