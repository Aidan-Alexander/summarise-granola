/**
 * Markdown → formatted Google Doc converter.
 *
 * Receives JSON: { "title": "...", "markdown": "..." }
 * Creates a new Google Doc with the markdown rendered into proper
 * Google Docs formatting (headings, bold/italic, lists, links, etc).
 *
 * Uses only DocumentApp — no Drive REST API, no OAuth scope juggling.
 */

function doPost(e) {
  var data  = JSON.parse(e.postData.contents);
  var title = data.title    || "Untitled summary";
  var md    = data.markdown || "";

  var doc  = DocumentApp.create(title);
  var body = doc.getBody();

  renderMarkdown(body, md);

  // Remove the initial empty paragraph DocumentApp.create() adds.
  if (body.getNumChildren() > 1) {
    var first = body.getChild(0);
    if (first.getType() === DocumentApp.ElementType.PARAGRAPH &&
        first.asParagraph().getText() === "") {
      body.removeChild(first);
    }
  }

  doc.saveAndClose();

  return ContentService.createTextOutput(
    JSON.stringify({ doc_id: doc.getId(), url: doc.getUrl() })
  ).setMimeType(ContentService.MimeType.JSON);
}

/* ---------- Markdown block rendering ---------- */

function renderMarkdown(body, md) {
  var lines = md.split("\n");
  var H = DocumentApp.ParagraphHeading;
  var HEADINGS = [H.HEADING1, H.HEADING2, H.HEADING3, H.HEADING4, H.HEADING5, H.HEADING6];

  var i = 0;
  while (i < lines.length) {
    var raw     = lines[i];
    var trimmed = raw.trim();

    // Skip blanks
    if (trimmed === "") { i++; continue; }

    // Fenced code blocks ``` ... ```
    if (/^```/.test(trimmed)) {
      var codeLines = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i].trim())) {
        codeLines.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++; // skip closing fence
      var codeP = body.appendParagraph(codeLines.join("\n"));
      codeP.editAsText()
        .setFontFamily("Consolas")
        .setBackgroundColor("#f1f3f4");
      continue;
    }

    // Horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      body.appendHorizontalRule();
      i++;
      continue;
    }

    // Heading
    var hMatch = trimmed.match(/^(#{1,6})\s+(.*)$/);
    if (hMatch) {
      var level = hMatch[1].length;
      var p = body.appendParagraph("");
      p.setHeading(HEADINGS[level - 1]);
      renderInline(p.editAsText(), hMatch[2]);
      i++;
      continue;
    }

    // Table: header row | --- | rows
    if (trimmed.indexOf("|") !== -1 &&
        i + 1 < lines.length &&
        /^\s*\|?[\s\-:|]+\|?\s*$/.test(lines[i + 1]) &&
        lines[i + 1].indexOf("-") !== -1) {
      var headerCells = parseTableRow(trimmed);
      i += 2; // skip header + separator
      var rows = [headerCells];
      while (i < lines.length && lines[i].trim().indexOf("|") !== -1) {
        rows.push(parseTableRow(lines[i].trim()));
        i++;
      }
      var tbl = body.appendTable(rows);
      // Bold the header row
      var headerRow = tbl.getRow(0);
      for (var c = 0; c < headerRow.getNumCells(); c++) {
        headerRow.getCell(c).editAsText().setBold(true);
      }
      continue;
    }

    // Blockquote
    var qMatch = trimmed.match(/^>\s?(.*)$/);
    if (qMatch) {
      var p = body.appendParagraph("");
      p.setIndentStart(36).setIndentFirstLine(36);
      renderInline(p.editAsText(), qMatch[1]);
      i++;
      continue;
    }

    // Bullet list
    var bMatch = trimmed.match(/^[-*+]\s+(.*)$/);
    if (bMatch) {
      var indent = leadingSpaces(raw);
      var nesting = Math.min(Math.floor(indent / 2), 7);
      var li = body.appendListItem("");
      li.setGlyphType(DocumentApp.GlyphType.BULLET).setNestingLevel(nesting);
      renderInline(li.editAsText(), bMatch[1]);
      i++;
      continue;
    }

    // Numbered list
    var nMatch = trimmed.match(/^\d+\.\s+(.*)$/);
    if (nMatch) {
      var indent = leadingSpaces(raw);
      var nesting = Math.min(Math.floor(indent / 2), 7);
      var li = body.appendListItem("");
      li.setGlyphType(DocumentApp.GlyphType.NUMBER).setNestingLevel(nesting);
      renderInline(li.editAsText(), nMatch[1]);
      i++;
      continue;
    }

    // Default paragraph
    var p = body.appendParagraph("");
    renderInline(p.editAsText(), trimmed);
    i++;
  }
}

function leadingSpaces(s) {
  var m = s.match(/^( *)/);
  return m ? m[1].length : 0;
}

function parseTableRow(line) {
  var s = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  var parts = s.split("|");
  return parts.map(function (c) { return c.trim(); });
}

/* ---------- Inline rendering (bold, italic, code, links) ---------- */

function renderInline(textEl, str) {
  var tokens = parseInline(str);
  for (var i = 0; i < tokens.length; i++) {
    var t = tokens[i];
    if (!t.text) continue;
    var start = textEl.getText().length;
    textEl.appendText(t.text);
    var end = textEl.getText().length - 1;
    if (end < start) continue;

    // Always set state explicitly — appended text inherits formatting from
    // the previous range otherwise, which causes bold/italic to bleed.
    textEl.setBold(start, end, !!t.bold);
    textEl.setItalic(start, end, !!t.italic);

    if (t.code) {
      textEl.setFontFamily(start, end, "Consolas");
      textEl.setBackgroundColor(start, end, "#f1f3f4");
    }

    // Same for links: explicit null clears any inherited link.
    textEl.setLinkUrl(start, end, t.link || null);
  }
}

function parseInline(str) {
  var tokens = [];
  var i = 0, n = str.length;
  var buf = "";
  var bold = false, italic = false;

  function flush() {
    if (buf.length > 0) {
      tokens.push({ text: buf, bold: bold, italic: italic, code: false, link: null });
      buf = "";
    }
  }

  while (i < n) {
    var ch = str[i];

    // Escaped char
    if (ch === "\\" && i + 1 < n) {
      buf += str[i + 1];
      i += 2;
      continue;
    }

    // **bold**
    if (ch === "*" && str[i + 1] === "*") {
      flush();
      bold = !bold;
      i += 2;
      continue;
    }

    // *italic* or _italic_ — toggle on single * or _
    if (ch === "*" || ch === "_") {
      flush();
      italic = !italic;
      i++;
      continue;
    }

    // `code`
    if (ch === "`") {
      flush();
      var close = str.indexOf("`", i + 1);
      if (close === -1) { buf += ch; i++; continue; }
      tokens.push({
        text: str.substring(i + 1, close),
        bold: bold, italic: italic, code: true, link: null
      });
      i = close + 1;
      continue;
    }

    // [text](url)
    if (ch === "[") {
      var rb = str.indexOf("]", i + 1);
      if (rb !== -1 && str.charAt(rb + 1) === "(") {
        var rp = str.indexOf(")", rb + 2);
        if (rp !== -1) {
          flush();
          tokens.push({
            text: str.substring(i + 1, rb),
            bold: bold, italic: italic, code: false,
            link: str.substring(rb + 2, rp)
          });
          i = rp + 1;
          continue;
        }
      }
    }

    buf += ch;
    i++;
  }

  flush();
  return tokens;
}
