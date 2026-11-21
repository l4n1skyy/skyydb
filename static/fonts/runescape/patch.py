"""Gives RuneScape Quill 8 the punctuation it lacks, drawn from its own glyphs, so quotes don't
fall back to another font mid-sentence: curly quotes = its straight ones, en dash = its hyphen,
em dash = a flat 6px bar at the hyphen's height, ellipsis = three periods.
usage: python3 patch.py <RuneStar's RuneScape-Quill-8.ttf> RuneScape-Quill-8.ttf  (needs fontTools)"""
import sys
from fontTools.ttLib import TTFont
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphComponent

font = TTFont(sys.argv[1])
glyf, hmtx = font["glyf"], font["hmtx"]
maps = {"‘": "quotesingle", "’": "quotesingle", "“": "quotedbl", "”": "quotedbl", "–": "hyphen"}


def composite(name, part, offsets, advance):
    g = Glyph()
    g.numberOfContours, g.components = -1, []
    for x in offsets:
        c = GlyphComponent()
        c.glyphName, c.x, c.y, c.flags = part, x, 0, 0
        g.components.append(c)
    glyf[name] = g  # also adds it to the glyph order
    g.recalcBounds(glyf)
    hmtx[name] = (advance, g.xMin)


# The hyphen is a little step (two hyphens side by side read as a tilde), so the em dash is drawn flat
pen = TTGlyphPen(None)
pen.moveTo((1, 5)); pen.lineTo((7, 5)); pen.lineTo((7, 6)); pen.lineTo((1, 6)); pen.closePath()
glyf["emdash"] = pen.glyph()  # also adds it to the glyph order
hmtx["emdash"] = (8, 1)
composite("ellipsis", "period", (0, 4, 8), 12)  # spaced like "..."
maps |= {"—": "emdash", "…": "ellipsis"}
for table in font["cmap"].tables:
    if table.isUnicode():
        table.cmap.update({ord(c): g for c, g in maps.items()})
font["maxp"].numGlyphs = len(font.getGlyphOrder())
font.save(sys.argv[2])
