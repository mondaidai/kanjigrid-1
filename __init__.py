#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Upstream: https://github.com/kelpso/kanjigrid
# AnkiWeb:  https://ankiweb.net/shared/info/909972618

import enum
import operator
import os
import re
import time
import types
import unicodedata
import urllib.parse
from functools import reduce

from anki.utils import ids2str
from aqt import mw
from aqt.utils import showInfo, showCritical
from aqt.webview import AnkiWebView
from aqt.qt import (Qt, QAction, QStandardPaths,
                    QImage, QPainter, QSize, QEvent, QSizePolicy,
                    QFileDialog, QDialog, QHBoxLayout, QVBoxLayout, QGroupBox,
                    QLineEdit, QLabel, QCheckBox, QSpinBox, QComboBox, QPushButton)

from . import data

# FIXME: changing the defaults using addons ui requires restarting anki to take effect
# TODO: config window based on the widget from KanjiGrid.setup
#       (s/Settings/Defaults/; s/Generate/Save/; s/default: .*//)

_css = "body { background: #ccc url(/img/noise.png); }" + \
    ".info-wrapper { height: auto; width: 500px; margin: 4em auto; padding: 0 0 2em 0; position: relative; }" + \
    ".info { max-height: 120px; height: auto; padding: .5em 0; border-bottom: solid 1px #fff; border-radius: 0 0 1em 1em;" + \
    "	overflow: hidden; position: relative; transition: 1s; } p { margin: 1em; }" + \
    ".info:after, .aftershadow { bottom: 0; width: 100%; height: 3em; border-radius: 0 0 1em 1em; position: absolute;" + \
    "	background: linear-gradient(rgba(192,192,192,0), #ccc); content: ''; }" + \
    ".aftershadow { filter: progid:DXImageTransform.Microsoft.gradient(startColorstr=#00cccccc, endColorstr=#ffcccccc); }" + \
    ".info-wrapper input[type=checkbox] { display: none; } .info-wrapper label { left: 50%; bottom: 1.5em; width: 9em;" + \
    "	height: 1.25em; margin:  0 0 0 -4.5em; border-bottom: solid 1px #fff; border-radius: 0 0 1em 1em; overflow: hidden;" + \
    "	position: absolute; font: 700 .67em/1.25em Arial; text-align: center; text-shadow: 0 1px 0 #fff; cursor: pointer; }" + \
    ".info-wrapper label .more { margin: -.1em 0 .35em; transition: 1s; } .info-wrapper .switch { width: 4em; display: inline-block; }" + \
    ".info-wrapper input[type=checkbox]:checked ~ .info { max-height: 15em; } .info-wrapper input[type=checkbox]:checked + label .more { margin-top: -1.65em; }"


class TestedUnit:
    def __init__(self, value):
        self.idx = 0
        self.value = value
        self.avg_interval = 0.0
        self.due = 0.0
        self.odue = 0.0
        self.count = 0
        self.mod = 0

    def addDataFromCard(self, idx, card, timeNow):
        if card.type > 0:
            newTotal = (self.avg_interval * self.count) + card.ivl

            self.count += 1
            self.avg_interval = newTotal / self.count
        if card.type == 2:
            if card.due < self.due or self.due == 0:
                self.due = card.due

            if card.odue < self.odue or self.odue == 0:
                self.odue = card.odue
                self.mod = self.odue

        if idx < self.idx or self.idx == 0:
            self.idx = idx


cjk_re = re.compile("CJK (UNIFIED|COMPATIBILITY) IDEOGRAPH")
def isKanji(unichar):
    return bool(cjk_re.match(unicodedata.name(unichar, "")))


def scoreAdjust(score):
    score += 1
    return 1 - 1 / (score * score)


def addUnitData(units, unitKey, i, card, kanjionly, timeNow):
    validKey = data.ignore.find(unitKey) == -1 and (not kanjionly or isKanji(unitKey))
    if validKey:
        if unitKey not in units:
            unit = TestedUnit(unitKey)
            units[unitKey] = unit

        units[unitKey].addDataFromCard(i, card, timeNow)


def hsvrgbstr(h, s=0.8, v=0.9):
    _256 = lambda x: round(x*256)
    i = int(h*6.0)
    f = (h*6.0) - i
    p = v*(1.0 - s)
    q = v*(1.0 - s*f)
    t = v*(1.0 - s*(1.0-f))
    i = i % 6
    if i == 0: return "#%0.2X%0.2X%0.2X" % (_256(v), _256(t), _256(p))
    if i == 1: return "#%0.2X%0.2X%0.2X" % (_256(q), _256(v), _256(p))
    if i == 2: return "#%0.2X%0.2X%0.2X" % (_256(p), _256(v), _256(t))
    if i == 3: return "#%0.2X%0.2X%0.2X" % (_256(p), _256(q), _256(v))
    if i == 4: return "#%0.2X%0.2X%0.2X" % (_256(t), _256(p), _256(v))
    if i == 5: return "#%0.2X%0.2X%0.2X" % (_256(v), _256(p), _256(q))


class KanjiGridWebView(AnkiWebView):
    def __init__(self, parent=None):
        super().__init__()
        # Saved images are empty if the background is transparent; AnkiWebView
        # sets bg color to transparent by default
        self.page().setBackgroundColor(Qt.GlobalColor.white)
        self.save_png = ()

    def eventFilter(self, obj, evt):
        if not(evt.type() == QEvent.Type.Paint and self.save_png):
            return super().eventFilter(obj, evt)

        filename, oldsize = self.save_png
        self.save_png = ()

        size = self.page().contentsSize().toSize()
        image = QImage(size, QImage.Format_ARGB32)
        painter = QPainter(image)
        self.render(painter)
        painter.end()
        success = image.save(filename, "png")
        self.resize(oldsize)
        mw.progress.finish()
        if success:
            showInfo("Image saved to %s!" % os.path.abspath(filename))
        else:
            showCritical("Failed to save the image.")
        return super().eventFilter(obj, evt)


class SortOrder(enum.Enum):
    NONE = 0
    UNICODE = 1
    SCORE = 2
    FREQUENCY = 3

    def pretty_value(self):
        return (
            "order found",
            "unicode order",
            "score",
            "frequency",
        )[self.value]


class KanjiGrid:
    def __init__(self, mw):
        if mw:
            self.menuAction = QAction("Generate Kanji Grid", mw, triggered=self.setup)
            mw.form.menuTools.addSeparator()
            mw.form.menuTools.addAction(self.menuAction)

    def generate(self, config, units, timeNow, saveMode=False):
        def kanjitile(char, index, count=0, avg_interval=0, missing=False):
            tile = ""
            score = "NaN"

            if avg_interval:
                score = round(scoreAdjust(avg_interval / config.interval), 2)

            if count != 0:
                bgcolour = hsvrgbstr(scoreAdjust(avg_interval / config.interval)/2)
            elif missing:
                bgcolour = "#FCE8E6"
            else:
                bgcolour = "#FFF"

            tile_class = "kanji-tile missing" if missing else "kanji-tile"
            if config.tooltips:
                tooltip = "Character: %s" % unicodedata.name(char)
                if missing:
                    tooltip += " | Not present in selected deck"
                if count:
                    tooltip += " | Count: %s | " % count
                    tooltip += "Avg Interval: %s | Score: %s | " % (round(avg_interval, 2), score)
                    tooltip += "Background: %s | Index: %s" % (bgcolour, index)
                tile += "\t<td class=\"%s\" style=\"--tile-color:%s\" title=\"%s\">" % (tile_class, bgcolour, tooltip)
            else:
                tile += "\t<td class=\"%s\" style=\"--tile-color:%s\">" % (tile_class, bgcolour)
            tile += "<a href=\"http://jisho.org/search/%s%%20%%23kanji\">%s</a></td>\n" % (char, char)

            return tile

        deckname = mw.col.decks.name(config.did).rsplit('::', 1)[-1]
        if saveMode:
            cols = config.wide
        else:
            cols = config.thin
        self.html  = "<!doctype html><html><head><meta charset=\"UTF-8\" /><title>Anki Kanji Grid</title>"
        self.html += "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />"
        self.html += "<style type=\"text/css\">"
        self.html += "*{box-sizing:border-box}body{margin:0;padding:32px 24px 48px;background:#f3f5f9;color:#263248;font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}.page{width:100%;max-width:2200px;margin:0 auto}.page-title{margin:0;color:#19253a;font-size:clamp(2rem,4vw,3.4rem);font-weight:750;letter-spacing:-.04em}.deck-name{margin:2px 0 22px;color:#758198;font-size:1.15rem}.legend{display:flex;flex-wrap:wrap;align-items:center;gap:12px 24px;margin:0 0 30px;padding:18px 22px;border:1px solid #e1e6ef;border-radius:16px;background:#fff;box-shadow:0 5px 18px #24334b0b}.legend-title{font-weight:700}.legend-scale{display:flex;align-items:center;gap:3px}.key{display:inline-block;width:28px;height:18px;border:1px solid #0000000d;border-radius:4px}.legend-note{color:#68758b;font-size:.9rem}.missing-key{display:inline-block;width:22px;height:22px;border:1px dashed #bd6262;border-radius:5px;background:#fce8e6;vertical-align:middle}.group{margin:0 0 26px;padding:20px 20px 22px;border:1px solid #e1e6ef;border-radius:16px;background:#fff;box-shadow:0 5px 18px #24334b0b;overflow-x:auto}.group h2{margin:0;color:#243550;font-size:1.55rem;font-weight:700}.group h4{margin:3px 0 14px;color:#7a879a;font-size:.92rem;font-weight:500}.maintable{margin:0 auto;border-spacing:4px;table-layout:fixed}.maintable tr{height:48px}.kanji-tile{width:46px;height:46px;padding:0;border:1px solid #e7ebf1;border-radius:9px;background:var(--tile-color,#fff);text-align:center;vertical-align:middle;transition:transform .12s ease,box-shadow .12s ease}.kanji-tile:hover{position:relative;z-index:1;transform:translateY(-2px);box-shadow:0 4px 12px #24334b20}.kanji-tile a,.kanji-tile a:visited{display:flex;width:100%;height:100%;align-items:center;justify-content:center;color:#182338;text-decoration:none;font-size:1.65rem;line-height:1}.kanji-tile.missing{border:1px dashed #bd6262;background:var(--tile-color,#fce8e6)}.kanji-tile.missing a,.kanji-tile.missing a:visited{color:#a34a4a}.unclassified{margin-top:30px}.datasource{display:block;margin:14px auto 0;color:#8590a1;font-size:.8rem;text-align:center;overflow-wrap:break-word}.datasource a{color:#506b9a}.datasource a:hover{text-decoration:underline}@media(max-width:700px){body{padding:18px 12px 30px}.group{padding:14px 10px}.legend{padding:14px}.kanji-tile{width:38px;height:40px}.maintable tr{height:42px}.kanji-tile a,.kanji-tile a:visited{font-size:1.4rem}}</style>"
        self.html += "</head>\n"
        self.html += "<body><main class=\"page\">\n"
        self.html += "<h1 class=\"page-title\">Kanji Grid</h1><p class=\"deck-name\">Deck: %s</p>\n" % deckname
        self.html += "<div class=\"legend\"><span class=\"legend-title\">Color guide</span><span class=\"legend-note\">Review strength:</span><span class=\"legend-note\">Weaker</span><span class=\"legend-scale\">"
        for c in [n/6.0 for n in range(6+1)]:
            self.html += "<span class=\"key\" style=\"background-color: %s;\">&nbsp;</span>" % hsvrgbstr(c/2)
        self.html += "</span><span class=\"legend-note\">Stronger</span><span class=\"legend-note\">White = not yet reviewed</span><span class=\"legend-note\"><span class=\"missing-key\"></span> Not in this deck</span></div>\n"
        if config.groupby >= len(SortOrder):
            groups = data.groups[config.groupby - len(SortOrder)]
            gc = 0
            kanji = [u.value for u in units.values()]
            for i in range(1, len(groups.data)):
                self.html += "<section class=\"group\"><h2>%s Kanji</h2>\n" % groups.data[i][0]
                table = "<table class=\"maintable\"><tr>\n"
                count = -1
                for unit in [units[c] for c in groups.data[i][1] if c in kanji]:
                    if unit.count != 0 or config.unseen:
                        count += 1
                        if count % cols == 0 and count != 0:
                            table += "</tr>\n<tr>\n"
                        table += kanjitile(unit.value, count, unit.count, unit.avg_interval)
                n = count+1
                t = len(groups.data[i][1])
                gc += n
                if config.include_missing:
                    missing = [c for c in groups.data[i][1] if c not in kanji]
                    for char in missing:
                        count += 1
                        if count % cols == 0 and count != 0:
                            table += "</tr>\n<tr>\n"
                        table += kanjitile(char, count, missing=True)
                    table += "</tr></table>\n"
                elif config.unseen:
                    table += "</tr></table>\n"
                    table += "<details><summary>Missing kanji</summary><table style=\"max-width:75%;\"><tr>\n"
                    count = -1
                    for char in [c for c in groups.data[i][1] if c not in kanji]:
                        count += 1
                        if count % cols == 0 and count != 0:
                            table += "</tr>\n<tr>\n"
                        table += kanjitile(char, count, missing=True)
                    if count == -1:
                        table += "<td><b style=\"color:#CCC\">None</b></td>"
                    table += "</tr></table></details>\n"
                else:
                    table += "</tr></table>\n"
                percent = n*100.0/t if t else 0.0
                self.html += "<h4>%d of %d - %0.2f%%</h4>\n" % (n, t, percent)
                self.html += table + "</section>\n"

            chars = reduce(lambda x, y: x+y, dict(groups.data).values())
            self.html += "<section class=\"group unclassified\"><h2>%s Kanji</h2>" % groups.data[0][0]
            table = "<table class=\"maintable\"><tr>\n"
            count = -1
            for unit in [u for u in units.values() if u.value not in chars]:
                if unit.count != 0 or config.unseen:
                    count += 1
                    if count % cols == 0 and count != 0:
                        table += "</tr>\n<tr>\n"
                    table += kanjitile(unit.value, count, unit.count, unit.avg_interval)
            table += "</tr></table>\n"
            n = count+1
            percent = n*100.0/gc if gc else 0.0
            self.html += "<h4>%d of %d - %0.2f%%</h4>\n" % (n, gc, percent)
            self.html += table + "</section>\n"
            self.html += "<span class=\"datasource\">Data source: " + ' '.join("<a href=\"{}\">{}</a>".format(w, urllib.parse.unquote(w)) if re.match("https?://", w) else w for w in groups.source.split(' ')) + "</span>"
        else:
            self.html += "<section class=\"group\"><h2>All Kanji</h2>\n"
            table = "<table class=\"maintable\"><tr>\n"
            unitsList = {
                SortOrder.NONE:      sorted(units.values(), key=lambda unit: (unit.idx, unit.count)),
                SortOrder.UNICODE:   sorted(units.values(), key=lambda unit: (unicodedata.name(unit.value), unit.count)),
                SortOrder.SCORE:     sorted(units.values(), key=lambda unit: (scoreAdjust(unit.avg_interval / config.interval), unit.count), reverse=True),
                SortOrder.FREQUENCY: sorted(units.values(), key=lambda unit: (unit.count, scoreAdjust(unit.avg_interval / config.interval)), reverse=True),
            }[SortOrder(config.groupby)]
            count = -1
            for unit in unitsList:
                if unit.count != 0 or config.unseen:
                    count += 1
                    if count % cols == 0 and count != 0:
                        table += "</tr>\n<tr>\n"
                    table += kanjitile(unit.value, count, unit.count, unit.avg_interval)
            table += "</tr></table>\n"
            self.html += "<h4>%d total unique kanji</h4>\n" % (count+1)
            self.html += table + "</section>\n"
        self.html += "</main></body></html>\n"

    def displaygrid(self, config, units, timeNow):
        self.generate(config, units, timeNow)
        self.timepoint("HTML generated")
        self.win = QDialog(mw)
        self.wv = KanjiGridWebView()
        vl = QVBoxLayout()
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self.wv)
        self.wv.stdHtml(self.html)
        hl = QHBoxLayout()
        vl.addLayout(hl)
        sh = QPushButton("Save HTML", clicked=lambda: self.savehtml(config))
        hl.addWidget(sh)
        sp = QPushButton("Save Image", clicked=self.savepng)
        hl.addWidget(sp)
        bb = QPushButton("Close", clicked=self.win.reject)
        hl.addWidget(bb)
        self.win.setLayout(vl)
        self.win.resize(1280, 850)
        self.timepoint("Window complete")
        return 0

    def savehtml(self, config):
        desktop = QStandardPaths.standardLocations(QStandardPaths.StandardLocation.DesktopLocation)[0]
        fileName = QFileDialog.getSaveFileName(self.win, "Save Page", desktop, "Web Page (*.html *.htm)")[0]
        if fileName != "":
            mw.progress.start(immediate=True)
            if ".htm" not in fileName:
                fileName += ".html"
            with open(fileName, 'w', encoding='utf-8') as fileOut:
                (units, timeNow) = self.kanjigrid(config)
                self.generate(config, units, timeNow, True)
                fileOut.write(self.html)
            mw.progress.finish()
            showInfo("Page saved to %s!" % os.path.abspath(fileOut.name))

    def savepng(self):
        desktop = QStandardPaths.standardLocations(QStandardPaths.StandardLocation.DesktopLocation)[0]
        fileName = QFileDialog.getSaveFileName(self.win, "Save Page", desktop, "Portable Network Graphics (*.png)")[0]
        if fileName != "":
            mw.progress.start(immediate=True)
            if ".png" not in fileName:
                fileName += ".png"

            oldsize = self.wv.size()
            self.wv.resize(self.wv.page().contentsSize().toSize())
            # the file will be saved after the page gets redrawn (KanjiGridWebView.eventFilter)
            self.wv.save_png = (fileName, oldsize)

    def kanjigrid(self, config):
        dids = [config.did]
        for _, id_ in mw.col.decks.children(config.did):
            dids.append(id_)
        self.timepoint("Decks selected")
        cids = mw.col.db.list("select id from cards where did in %s or odid in %s" % (ids2str(dids), ids2str(dids)))
        self.timepoint("Cards selected")

        units = dict()
        notes = dict()
        timeNow = time.time()
        for i in cids:
            card = mw.col.getCard(i)
            if card.nid not in notes.keys():
                keys = card.note().keys()
                unitKey = set()
                matches = operator.eq if config.literal else operator.contains
                for keyword in config.pattern:
                    for key in keys:
                        if matches(key.lower(), keyword):
                            unitKey.update(set(card.note()[key]))
                            break
                notes[card.nid] = unitKey
            else:
                unitKey = notes[card.nid]
            if unitKey is not None:
                for ch in unitKey:
                    addUnitData(units, ch, i, card, config.kanjionly, timeNow)
        self.timepoint("Units created")
        return units, timeNow

    def makegrid(self, config):
        self.time = time.time()
        self.timepoint("Start")
        (units, timeNow) = self.kanjigrid(config)
        if units is not None:
            self.displaygrid(config, units, timeNow)

    def setup(self):
        addonconfig = mw.addonManager.getConfig(__name__)
        config = types.SimpleNamespace(**addonconfig['defaults'])
        last_used = addonconfig.get("last_used", {})
        if isinstance(last_used, dict):
            for key in ("pattern", "literal", "interval", "thin", "wide", "groupby",
                        "unseen", "include_missing", "tooltips"):
                if key in last_used:
                    setattr(config, key, last_used[key])
        if addonconfig.get("_debug_time", False):
            self.timepoint = lambda c: print("%s: %0.3f" % (c, time.time()-self.time))
        else:
            self.timepoint = lambda _: None
        config.did = mw.col.conf['curDeck']

        swin = QDialog(mw)
        vl = QVBoxLayout()
        fl = QHBoxLayout()
        deckcb = QComboBox()
        deck_names = sorted(mw.col.decks.allNames())
        deckcb.addItems(deck_names)
        deckcb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        fl.addWidget(QLabel("Deck: "))
        saved_deck = last_used.get("deck") if isinstance(last_used, dict) else None
        if saved_deck in deck_names:
            config.did = mw.col.decks.byName(saved_deck)['id']
        selected_deck = mw.col.decks.get(config.did)['name']
        deckcb.setCurrentText(selected_deck)
        def change_did(deckname):
            config.did = mw.col.decks.byName(deckname)['id']
        deckcb.currentTextChanged.connect(change_did)
        fl.addWidget(deckcb)
        vl.addLayout(fl)
        frm = QGroupBox("Settings")
        vl.addWidget(frm)
        il = QVBoxLayout()
        fl = QHBoxLayout()
        field = QLineEdit()
        field.setText(" ".join(config.pattern) if isinstance(config.pattern, list) else config.pattern)
        field.setPlaceholderText("e.g. \"kanji\" or \"sentence-kanji\" (default: \"%s\")" % config.pattern)
        il.addWidget(QLabel("Pattern or Field names to search for (case insensitive):"))
        fl.addWidget(field)
        liter = QCheckBox("Match exactly")
        liter.setChecked(config.literal)
        fl.addWidget(liter)
        il.addLayout(fl)
        stint = QSpinBox()
        stint.setRange(1, 65536)
        stint.setValue(config.interval)
        il.addWidget(QLabel("Card interval considered strong:"))
        il.addWidget(stint)
        ttcol = QSpinBox()
        ttcol.setRange(1, 99)
        ttcol.setValue(config.thin)
        il.addWidget(QLabel("Number of Columns in the in-app table:"))
        il.addWidget(ttcol)
        wtcol = QSpinBox()
        wtcol.setRange(1, 99)
        wtcol.setValue(config.wide)
        il.addWidget(QLabel("Number of Columns in the exported table:"))
        il.addWidget(wtcol)
        groupby = QComboBox()
        groupby.addItems([
            *("None, sorted by " + x.pretty_value() for x in SortOrder),
            *(x.name for x in data.groups),
        ])
        groupby.setCurrentIndex(config.groupby)
        il.addWidget(QLabel("Group by:"))
        il.addWidget(groupby)
        shnew = QCheckBox("Show units not yet seen")
        shnew.setChecked(config.unseen)
        il.addWidget(shnew)
        missingcb = QCheckBox("Include kanji not in deck in grouped grids")
        missingcb.setChecked(config.include_missing)
        il.addWidget(missingcb)
        toolt = QCheckBox("Show informational tooltips")
        toolt.setChecked(config.tooltips)
        il.addWidget(toolt)
        frm.setLayout(il)
        hl = QHBoxLayout()
        vl.addLayout(hl)
        gen = QPushButton("Generate", clicked=swin.accept)
        hl.addWidget(gen)
        cls = QPushButton("Close", clicked=swin.reject)
        hl.addWidget(cls)
        swin.setLayout(vl)
        swin.setTabOrder(gen, cls)
        swin.setTabOrder(cls, field)
        swin.setTabOrder(field, liter)
        swin.setTabOrder(liter, stint)
        swin.setTabOrder(stint, ttcol)
        swin.setTabOrder(ttcol, wtcol)
        swin.setTabOrder(wtcol, groupby)
        swin.setTabOrder(groupby, shnew)
        swin.setTabOrder(shnew, missingcb)
        swin.setTabOrder(missingcb, toolt)
        swin.resize(500, 400)
        if swin.exec():
            mw.progress.start(immediate=True)
            pattern = field.text().strip() or (" ".join(config.pattern) if isinstance(config.pattern, list) else config.pattern)
            config.pattern = pattern.lower()
            config.did = mw.col.decks.byName(deckcb.currentText())['id']
            addonconfig["last_used"] = {
                "pattern": config.pattern,
                "deck": deckcb.currentText(),
                "literal": liter.isChecked(),
                "interval": stint.value(),
                "thin": ttcol.value(),
                "wide": wtcol.value(),
                "groupby": groupby.currentIndex(),
                "unseen": shnew.isChecked(),
                "include_missing": missingcb.isChecked(),
                "tooltips": toolt.isChecked(),
            }
            mw.addonManager.writeConfig(__name__, addonconfig)
            config.pattern = config.pattern.split()
            config.literal = liter.isChecked()
            config.interval = stint.value()
            config.thin = ttcol.value()
            config.wide = wtcol.value()
            config.groupby = groupby.currentIndex()
            config.unseen = shnew.isChecked()
            config.include_missing = missingcb.isChecked()
            config.tooltips = toolt.isChecked()
            self.makegrid(config)
            mw.progress.finish()
            self.win.show()

if __name__ != "__main__":
    # Save a reference to the toolkit onto the mw, preventing garbage collection of PyQt objects
    if mw:
        mw.kanjigrid = KanjiGrid(mw)
else:
    print("This is an addon for the Anki spaced repetition learning system and cannot be run directly.")
    print("Please download Anki from <http://ankisrs.net/>")

# vim:expandtab:
