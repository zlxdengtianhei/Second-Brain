"""`sb export`: turn the cards of one map (MOC) into one article.

The steps are separate modules so each can be tested alone:

* :mod:`.skeleton` (program) - sections, sub-sections and writing units from
  the taxonomy and the prerequisite order of the map page;
* :mod:`.pipeline` (models) - write each unit, review it in another run, repair
  within the review policy's rounds, then one smoothing call (introduction,
  transitions between sections, which duplicate paragraph to drop);
* :mod:`.checks` (program) - citations, numbers, formulas and links;
* :mod:`.render` (program) - Markdown and optional HTML.

An article is a view: it is rebuilt from the cards, never edited by hand, and
never written into the knowledge folder.
"""
