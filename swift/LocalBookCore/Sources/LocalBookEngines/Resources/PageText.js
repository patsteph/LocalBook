// LocalBook's page reader, run inside the loaded page after Readability.js (same script).
// Gives what the old app's trafilatura call gave the chunker: one block per line, list items as "- …",
// tables as Markdown rows, then the "=== IMAGE REFERENCES ===" section from web_scraper._extract_image_references.
(function () {
  var SKIP = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEMPLATE: 1, SVG: 1, IFRAME: 1, CANVAS: 1, VIDEO: 1, AUDIO: 1, OBJECT: 1, BUTTON: 1, SELECT: 1, INPUT: 1, TEXTAREA: 1 };
  var BLOCK = { P: 1, H1: 1, H2: 1, H3: 1, H4: 1, H5: 1, H6: 1, LI: 1, BLOCKQUOTE: 1, DT: 1, DD: 1, FIGCAPTION: 1, DIV: 1, SECTION: 1, ARTICLE: 1,
                HEADER: 1, FOOTER: 1, ASIDE: 1, MAIN: 1, NAV: 1, UL: 1, OL: 1, DL: 1, HR: 1, ADDRESS: 1, SUMMARY: 1, DETAILS: 1, FIGURE: 1, FORM: 1, LABEL: 1 };
  var STRIP = { NAV: 1, HEADER: 1, FOOTER: 1, ASIDE: 1 };

  function collapse(s) { return (s || "").replace(/\s+/g, " ").trim(); }

  // Every block of `root` as a line of text; tables become Markdown rows, as trafilatura wrote them.
  function linesOf(root) {
    var lines = [], buf = "", pending = "";
    function flush() {
      var s = collapse(buf); buf = "";
      if (s) { lines.push(pending + s); pending = ""; }
    }
    function table(t) {
      var rows = [];
      var trs = t.querySelectorAll("tr");
      for (var i = 0; i < trs.length; i++) {
        var tr = trs[i];
        if (tr.closest("table") !== t) continue;
        var cells = [], cs = tr.querySelectorAll("th,td");
        for (var j = 0; j < cs.length; j++) {
          if (cs[j].closest("tr") === tr) cells.push(collapse(cs[j].textContent).replace(/\|/g, "/"));
        }
        if (cells.join("")) rows.push(cells);
      }
      if (!rows.length) return;
      lines.push("| " + rows[0].join(" | ") + " |");
      var sep = [];
      for (var k = 0; k < rows[0].length; k++) sep.push("---");
      lines.push("|" + sep.join("|") + "|");
      for (var r = 1; r < rows.length; r++) lines.push("| " + rows[r].join(" | ") + " |");
    }
    function walk(node) {
      if (node.nodeType === 3) { buf += node.nodeValue; return; }
      if (node.nodeType !== 1) return;
      var tag = node.tagName;
      if (SKIP[tag]) return;
      if (tag === "TABLE") { flush(); table(node); return; }
      if (tag === "BR") { flush(); return; }
      if (tag === "PRE") {
        flush();
        var ls = node.textContent.split("\n");
        for (var i = 0; i < ls.length; i++) { var l = collapse(ls[i]); if (l) lines.push(l); }
        return;
      }
      var block = !!BLOCK[tag];
      if (block) flush();
      if (tag === "LI") pending = "- ";
      var kids = node.childNodes;
      for (var c = 0; c < kids.length; c++) walk(kids[c]);
      if (block) { flush(); pending = ""; }
    }
    walk(root);
    flush();
    return lines;
  }

  // web_scraper._extract_image_references: figure captions first, then alt text, capped at 20.
  function imageReferences(root) {
    var refs = [], seen = {};
    var decorative = { image: 1, photo: 1, picture: 1, icon: 1, logo: 1, banner: 1, avatar: 1, thumbnail: 1, img: 1 };
    var figs = root.querySelectorAll("figure");
    for (var i = 0; i < figs.length; i++) {
      var cap = figs[i].querySelector("figcaption"), img = figs[i].querySelector("img");
      var caption = cap ? collapse(cap.textContent) : "";
      var alt = img ? collapse(img.getAttribute("alt")) : "";
      var desc = caption || alt;
      if (!desc || desc.length < 5) continue;
      var key = desc.slice(0, 60).toLowerCase();
      if (seen[key]) continue;
      seen[key] = 1;
      if (alt && caption) seen[alt.slice(0, 60).toLowerCase()] = 1;
      refs.push((caption ? "[FIGURE] " : "[IMAGE] ") + desc);
    }
    var imgs = root.querySelectorAll("img");
    for (var j = 0; j < imgs.length; j++) {
      var a = collapse(imgs[j].getAttribute("alt"));
      if (!a || a.length < 5 || decorative[a.toLowerCase()]) continue;
      var k = a.slice(0, 60).toLowerCase();
      if (seen[k]) continue;
      seen[k] = 1;
      refs.push("[IMAGE] " + a);
    }
    if (!refs.length) return "";
    return "\n\n=== IMAGE REFERENCES ===\n" + refs.slice(0, 20).join("\n");
  }

  function meta(selectors) {
    for (var i = 0; i < selectors.length; i++) {
      var el = document.querySelector(selectors[i]);
      if (!el) continue;
      var v = el.getAttribute("content") || el.getAttribute("datetime") || el.textContent;
      v = collapse(v);
      if (v) return v;
    }
    return "";
  }

  function wholePage() {
    var body = document.body ? document.body.cloneNode(true) : null;
    if (!body) return [];
    var strip = body.querySelectorAll("nav, header, footer, aside, [role=navigation], [role=banner], [role=contentinfo], [aria-hidden=true]");
    for (var i = 0; i < strip.length; i++) { if (strip[i].parentNode) strip[i].parentNode.removeChild(strip[i]); }
    return linesOf(body);
  }

  var article = null;
  try {
    var clone = document.cloneNode(true);
    article = new Readability(clone, { charThreshold: 100 }).parse();
  } catch (e) { article = null; }

  var articleLines = [];
  if (article && article.content) {
    var holder = document.implementation.createHTMLDocument("").body;
    holder.innerHTML = article.content;
    articleLines = linesOf(holder);
  }
  var pageLines = wholePage();
  var articleChars = articleLines.join("\n").length, pageChars = pageLines.join("\n").length;
  // Readability's article when it found one worth the name; the whole page when the article is a sliver of it
  // (index and documentation pages), the way trafilatura's fallback keeps the page.
  var useArticle = articleChars >= 200 && !(pageChars > 1000 && articleChars < 0.2 * pageChars);
  var lines = useArticle ? articleLines : pageLines;
  var text = lines.join("\n") + imageReferences(document);

  return {
    title: collapse((article && article.title) || document.title || ""),
    author: collapse((article && article.byline) || meta(['meta[name="author"]', 'meta[property="article:author"]'])),
    date: collapse((article && article.publishedTime) || meta(['meta[property="article:published_time"]', 'meta[name="date"]', 'meta[name="pubdate"]', 'meta[itemprop="datePublished"]', 'time[datetime]'])),
    text: text,
    readable: useArticle
  };
})();
