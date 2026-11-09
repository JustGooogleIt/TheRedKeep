"""The event discovery funnel (DESIGN.md 6.2, milestone M4, eval E0): from every listed market to the few that
could move a portfolio.

* ``labels``: cheap filters, an even sample of the catalogue and Claude's labels under a fixed rubric (is the market
  macro, and how would each factor move if it resolved yes rather than no).
* ``screen``: a fast model taught by those labels, which screens every market in milliseconds.
* ``e0``: whether the screen keeps the markets that matter, against Claude's labels on markets it never saw.
* ``run``: the commands: label a sample, evaluate the screen, screen the whole catalogue.
"""
