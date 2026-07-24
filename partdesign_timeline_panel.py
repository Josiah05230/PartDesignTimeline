"""PartDesignTimeline — a Fusion-360-style history scrubber (implementation module).

This is a *real importable module* (imported from InitGui.py) rather than code
exec'd directly by FreeCAD's addon loader. That matters: FreeCAD execs InitGui.py
with split globals/locals, so any deferred QTimer callback or widget method that
references module-level names (helpers, classes, _STATE) fails with NameError. As
an imported module, every function here gets this module's __dict__ as its globals,
so all references resolve correctly at runtime.

Dockable panel (bottom) for the active PartDesign Body:
- compact feature chips (type icon + short label), current position highlighted
- a draggable scrubber: drag it and the model rebuilds to that point in time
- click a chip to jump there; ctrl+click multi-select; double-click edit;
  right-click for rename / more.
"""
import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui
try:
    from PySide import QtWidgets
except Exception:  # Qt5 fallback
    QtWidgets = QtGui

_STATE = {"dock": None, "widget": None, "docobs": None, "selobs": None}
_DATUM = ('PartDesign::Plane', 'PartDesign::Line', 'PartDesign::Point',
          'PartDesign::CoordinateSystem')


def _active_body(doc):
    if doc is None:
        return None
    try:
        import PartDesignGui
        b = PartDesignGui.getActiveBody(False)
        if b is not None:
            return b
    except Exception:
        pass
    bodies = [o for o in doc.Objects if o.TypeId == 'PartDesign::Body']
    return bodies[0] if bodies else None


def _features(body):
    return [o for o in getattr(body, 'Group', []) if o.TypeId != 'App::Origin']


def _is_sketch(f):
    return f.TypeId == 'Sketcher::SketchObject'


def _is_datum(f):
    return f.TypeId in _DATUM


def _is_solid(f):
    return f.TypeId.startswith('PartDesign::') and f.TypeId not in _DATUM


def _qt_exec(widget, *args):
    """PySide6 exposes exec(); PySide2 used exec_(). Support whichever exists."""
    fn = getattr(widget, "exec", None) or getattr(widget, "exec_")
    return fn(*args)


class TimelineWidget(QtWidgets.QWidget):
    def __init__(self):
        super(TimelineWidget, self).__init__()
        self._guard = False
        self._feats = []
        self._body = None
        self._buttons = []
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(4, 2, 4, 2)
        v.setSpacing(1)
        # --- compact chip strip ---
        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._scroll.setFixedHeight(52)
        self._row = QtWidgets.QWidget()
        self._rlay = QtWidgets.QHBoxLayout(self._row)
        self._rlay.setContentsMargins(2, 0, 2, 0)
        self._rlay.setSpacing(0)
        self._scroll.setWidget(self._row)
        v.addWidget(self._scroll)
        # --- scrubber ---
        self._slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
        self._slider.setTickInterval(1)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(1)
        self._slider.setToolTip("Scrub through history — drag to rebuild the model to that step")
        self._slider.valueChanged.connect(self._on_slider)
        v.addWidget(self._slider)
        self.setMinimumHeight(78)
        self.refresh()

    def _clear(self):
        while self._rlay.count():
            it = self._rlay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

    def _short(self, label):
        return label if len(label) <= 11 else label[:10] + "…"

    def refresh(self):
        if self._guard:
            return
        try:
            self._clear()
            self._buttons = []
            body = _active_body(App.activeDocument())
            self._body = body
            if body is None:
                self._feats = []
                self._rlay.addWidget(QtWidgets.QLabel("  No active PartDesign Body  "))
                self._rlay.addStretch(1)
                self._slider.setEnabled(False)
                return
            self._feats = _features(body)
            self._slider.setEnabled(bool(self._feats))
            self._slider.blockSignals(True)
            self._slider.setMinimum(0)
            self._slider.setMaximum(max(0, len(self._feats) - 1))
            tip = getattr(body, 'Tip', None)
            cur = 0
            for i, feat in enumerate(self._feats):
                b = QtWidgets.QToolButton()
                b.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
                b.setAutoRaise(True)
                b.setFixedWidth(60)
                b.setIconSize(QtCore.QSize(22, 22))
                try:
                    ic = feat.ViewObject.Icon
                    if isinstance(ic, QtGui.QIcon):
                        b.setIcon(ic)
                except Exception:
                    pass
                b.setText(self._short(feat.Label))
                fnt = b.font()
                fnt.setPointSizeF(max(7.0, fnt.pointSizeF() - 1.0))
                b.setFont(fnt)
                b.setToolTip(
                    "%s (%s)\nClick/drag: rebuild to here   Ctrl+click: multi-select\n"
                    "Double-click: edit   Right-click: rename / more"
                    % (feat.Label, feat.Name))
                b.clicked.connect(lambda checked=False, idx=i: self._on_chip_click(idx))
                b._ft_feat = feat
                b._ft_idx = i
                b.installEventFilter(self)
                self._rlay.addWidget(b)
                self._buttons.append(b)
                if feat is tip:
                    cur = i
            self._rlay.addStretch(1)
            self._slider.setValue(cur)
            self._slider.blockSignals(False)
            self._restyle()
        except Exception as e:
            App.Console.PrintWarning("[PartDesignTimeline] refresh: %s\n" % e)

    def _restyle(self):
        # Highlight is style-based (not Qt's checkable auto-toggle, which got
        # "stuck" on). Current step = solid border; features in FreeCAD's own
        # selection = dashed border, so clicking empty space (which clears the
        # selection) also clears the chip highlight.
        try:
            sel = set(Gui.Selection.getSelection())
        except Exception:
            sel = set()
        cur = self._slider.value() if self._feats else -1
        for i, b in enumerate(self._buttons):
            feat = getattr(b, '_ft_feat', None)
            if i == cur:
                b.setStyleSheet("QToolButton{border:2px solid palette(highlight);border-radius:3px;}")
            elif feat is not None and feat in sel:
                b.setStyleSheet("QToolButton{border:1px dashed palette(highlight);border-radius:3px;}")
            else:
                b.setStyleSheet("")

    def _on_slider(self, val):
        self._apply(val)

    def _goto(self, idx):
        self._slider.blockSignals(True)
        self._slider.setValue(idx)
        self._slider.blockSignals(False)
        self._apply(idx)

    def _apply(self, idx):
        """Rebuild the model to the state at feature index `idx` (time travel)."""
        if self._guard or self._body is None or not self._feats:
            return
        if idx < 0 or idx >= len(self._feats):
            return
        self._guard = True
        try:
            feats = self._feats
            body = self._body
            target = feats[idx]
            # the model's solid state at this step = last solid feature at/before idx
            last_solid = None
            for j in range(idx, -1, -1):
                if _is_solid(feats[j]):
                    last_solid = feats[j]
                    break
            if last_solid is not None:
                try:
                    body.Tip = last_solid
                except Exception:
                    pass
            for f in feats:
                try:
                    f.Visibility = False
                except Exception:
                    pass
            if last_solid is not None:
                last_solid.Visibility = True
            # overlay the pointed-at sketch/datum so you see it at that moment
            if _is_sketch(target) or _is_datum(target):
                try:
                    target.Visibility = True
                except Exception:
                    pass
            body.Document.recompute()
            self._restyle()
            try:
                Gui.SendMsgToActiveView("ViewFit")
            except Exception:
                pass
        except Exception as e:
            App.Console.PrintWarning("[PartDesignTimeline] apply: %s\n" % e)
        finally:
            self._guard = False

    def _on_chip_click(self, idx):
        # Ctrl+click = multi-select (for grouping/ops); plain click = time-travel
        mods = QtWidgets.QApplication.keyboardModifiers()
        if mods & QtCore.Qt.ControlModifier:
            self._toggle_select(idx)
            self._restyle()
        else:
            self._goto(idx)

    def _toggle_select(self, idx):
        # add/remove the feature in FreeCAD's own selection so it highlights in
        # the 3D view + tree and can be grouped/operated on with native commands
        try:
            f = self._feats[idx]
            if f in Gui.Selection.getSelection():
                Gui.Selection.removeSelection(f)
            else:
                Gui.Selection.addSelection(f)
        except Exception as e:
            App.Console.PrintWarning("[PartDesignTimeline] select: %s\n" % e)

    def _context_menu(self, btn):
        feat = getattr(btn, '_ft_feat', None)
        if feat is None:
            return
        m = QtWidgets.QMenu()
        a_rename = m.addAction("Rename…")
        a_edit = m.addAction("Edit feature")
        a_vis = m.addAction("Toggle visibility")
        a_sel = m.addAction("Select in 3D / tree")
        m.addSeparator()
        a_recompute = m.addAction("Force recompute (regenerate)")
        act = _qt_exec(m, btn.mapToGlobal(btn.rect().bottomLeft()))
        try:
            if act == a_rename:
                self._rename(feat)
            elif act == a_edit:
                Gui.activeDocument().setEdit(feat)
            elif act == a_vis:
                feat.Visibility = not feat.Visibility
                feat.Document.recompute()
            elif act == a_sel:
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(feat)
            elif act == a_recompute:
                self._recompute_all()
        except Exception as e:
            App.Console.PrintWarning("[PartDesignTimeline] menu: %s\n" % e)

    def _rename(self, feat):
        try:
            # A plain QDialog, not QInputDialog: QInputDialog ignores
            # setMinimumSize/resize (it snaps to its own sizeHint), so on
            # Wayland/COSMIC it renders cramped no matter what. A QDialog with an
            # explicit layout honors the minimum width we set.
            dlg = QtWidgets.QDialog(self)
            dlg.setWindowTitle("Rename feature")
            dlg.setSizeGripEnabled(True)
            lay = QtWidgets.QVBoxLayout(dlg)
            lay.addWidget(QtWidgets.QLabel("New name:"))
            edit = QtWidgets.QLineEdit(feat.Label)
            edit.selectAll()
            # The child's minimum width is what the layout truly cannot shrink
            # below — the most reliable size driver on Wayland/COSMIC, where the
            # dialog's own minimum/resize hints are often ignored on first map.
            edit.setMinimumWidth(360)
            lay.addWidget(edit)
            bb = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            bb.accepted.connect(dlg.accept)
            bb.rejected.connect(dlg.reject)
            lay.addWidget(bb)
            dlg.setMinimumSize(420, 150)
            dlg.resize(420, 150)
            edit.setFocus(QtCore.Qt.OtherFocusReason)
            # Belt-and-suspenders for compositors that map the window before
            # honoring the requested size: re-assert it on the next event loop.
            QtCore.QTimer.singleShot(0, lambda: dlg.resize(420, 150))
            if _qt_exec(dlg) and edit.text().strip():
                feat.Label = edit.text().strip()
                self.refresh()
        except Exception as e:
            App.Console.PrintWarning("[PartDesignTimeline] rename: %s\n" % e)

    def _recompute_all(self):
        # help when inserting features mid-history doesn't fully regenerate:
        # touch everything and force a full recompute, then resync the panel
        try:
            doc = self._body.Document if self._body else App.activeDocument()
            for o in doc.Objects:
                try:
                    o.touch()
                except Exception:
                    pass
            doc.recompute(None, True, True)
            self.refresh()
            App.Console.PrintMessage("[PartDesignTimeline] forced full recompute.\n")
        except Exception as e:
            App.Console.PrintWarning("[PartDesignTimeline] recompute: %s\n" % e)

    def eventFilter(self, obj, ev):
        if hasattr(obj, '_ft_feat'):
            if ev.type() == QtCore.QEvent.MouseButtonDblClick:
                try:
                    Gui.activeDocument().setEdit(obj._ft_feat)
                except Exception as e:
                    App.Console.PrintWarning("[PartDesignTimeline] edit: %s\n" % e)
                return True
            if ev.type() == QtCore.QEvent.ContextMenu:
                self._context_menu(obj)
                return True
        return False


class _DocObserver(object):
    def __init__(self, w):
        self.w = w

    def _r(self, *a):
        try:
            self.w.refresh()
        except Exception:
            pass
    slotCreatedObject = _r
    slotDeletedObject = _r
    slotChangedObject = _r
    slotActivateDocument = _r
    slotFinishRestoreDocument = _r
    slotDeletedDocument = _r


class _SelObserver(object):
    def __init__(self, w):
        self.w = w

    def _r(self, *a):
        # selection changed (incl. clicking empty space -> cleared): just re-style
        # the chips, no need to rebuild the whole strip
        try:
            self.w._restyle()
        except Exception:
            pass
    addSelection = _r
    removeSelection = _r
    setSelection = _r
    clearSelection = _r


def install_timeline():
    try:
        mw = Gui.getMainWindow()
        if mw is None:
            QtCore.QTimer.singleShot(1000, install_timeline)
            return
        if _STATE["dock"] is not None:
            # Already created (maybe the user closed it) — re-show + refresh so
            # View > Panels > Timeline reliably brings it back instead of no-op'ing.
            _STATE["dock"].setVisible(True)
            _STATE["dock"].raise_()
            _STATE["widget"].refresh()
            return
        dock = QtWidgets.QDockWidget("Timeline", mw)
        dock.setObjectName("PartDesignTimeline")
        w = TimelineWidget()
        dock.setWidget(w)
        mw.addDockWidget(QtCore.Qt.BottomDockWidgetArea, dock)
        docobs = _DocObserver(w)
        App.addDocumentObserver(docobs)
        selobs = _SelObserver(w)
        Gui.Selection.addObserver(selobs)
        _STATE.update(dock=dock, widget=w, docobs=docobs, selobs=selobs)
        App.Console.PrintMessage("[PartDesignTimeline] panel installed (bottom dock).\n")
    except Exception as e:
        App.Console.PrintWarning("[PartDesignTimeline] install failed: %s\n" % e)
