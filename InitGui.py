"""PartDesignTimeline — loader.

FreeCAD execs this InitGui.py with split globals/locals, which breaks any deferred
callback that references module-level names (e.g. "name '_STATE' is not defined").
So keep this file trivial: put our addon dir on sys.path, import the real
implementation as a proper module (its functions then carry the module's own
globals), and schedule the dock install on that module's function.
"""
import os
import sys

try:
    _addon_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    import inspect
    _addon_dir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
if _addon_dir not in sys.path:
    sys.path.insert(0, _addon_dir)

from PySide import QtCore
import partdesign_timeline_panel

# defer until the main window + workbenches are up
QtCore.QTimer.singleShot(1500, partdesign_timeline_panel.install_timeline)
