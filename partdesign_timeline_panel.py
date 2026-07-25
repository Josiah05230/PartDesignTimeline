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


# internal drag payload for chip reordering (carries the source chip index)
_MIME = "application/x-partdesign-timeline-chip"


def _evt_pos(ev):
    """Local position of a mouse/drag event as a QPoint. Qt6/PySide6 removed the
    old .pos() on these events in favour of .position() (a QPointF)."""
    try:
        return ev.position().toPoint()
    except Exception:
        return ev.pos()


class TimelineWidget(QtWidgets.QWidget):
    def __init__(self):
        super(TimelineWidget, self).__init__()
        self._guard = False
        self._feats = []
        self._body = None
        self._buttons = []
        self._press_pos = None      # drag-to-reorder: where the press started
        self._drag_src_idx = None   # drag-to-reorder: which chip is being dragged
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
        # drag-to-reorder: the strip is a drop target, with a thin insertion line
        self._row.setAcceptDrops(True)
        self._row.installEventFilter(self)
        self._drop_line = QtWidgets.QFrame(self._row)
        self._drop_line.setFrameShape(QtWidgets.QFrame.VLine)
        self._drop_line.setStyleSheet("color: palette(highlight); background: palette(highlight);")
        self._drop_line.hide()
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
                    "%s (%s)\nClick: rebuild to here   Ctrl+click: multi-select\n"
                    "Drag: reorder in history   Double-click: edit   Right-click: more"
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

    # ------------------------------------------------------------------ #
    #  drag-to-reorder                                                    #
    # ------------------------------------------------------------------ #
    def _start_drag(self, btn):
        """Begin an internal drag of a chip so it can be dropped elsewhere."""
        if btn not in self._buttons:
            return
        self._drag_src_idx = self._buttons.index(btn)
        drag = QtGui.QDrag(btn)
        mime = QtCore.QMimeData()
        mime.setData(_MIME, QtCore.QByteArray(str(self._drag_src_idx).encode()))
        drag.setMimeData(mime)
        try:  # drag a ghost of the chip itself
            pm = btn.grab()
            drag.setPixmap(pm)
            drag.setHotSpot(QtCore.QPoint(pm.width() // 2, pm.height() // 2))
        except Exception:
            pass
        self._press_pos = None
        _qt_exec(drag, QtCore.Qt.MoveAction)   # blocks until dropped/cancelled
        self._hide_drop_indicator()

    def _drop_index(self, pos):
        """Insertion index (0..N) for a drop at x-position `pos` in the strip."""
        x = pos.x()
        idx = 0
        for b in self._buttons:
            if x >= b.x() + b.width() / 2.0:
                idx += 1
        return idx

    def _show_drop_indicator(self, pos):
        k = self._drop_index(pos)
        if k < len(self._buttons):
            xpos = self._buttons[k].x()
        elif self._buttons:
            last = self._buttons[-1]
            xpos = last.x() + last.width()
        else:
            xpos = 0
        self._drop_line.setGeometry(max(0, xpos - 1), 0, 2, self._row.height())
        self._drop_line.show()
        self._drop_line.raise_()

    def _hide_drop_indicator(self):
        try:
            self._drop_line.hide()
        except Exception:
            pass

    def _do_drop(self, pos):
        self._hide_drop_indicator()
        src = self._drag_src_idx
        self._drag_src_idx = None
        if src is None or src < 0 or src >= len(self._feats):
            return
        k = self._drop_index(pos)
        if k > src:          # removing the source first shifts later slots left
            k -= 1
        feat = self._feats[src]
        ok, msg = self._reorder(feat, k)
        if not ok:
            App.Console.PrintWarning(
                "[PartDesignTimeline] can't move %s here - %s\n" % (feat.Label, msg))
            try:
                Gui.getMainWindow().statusBar().showMessage(
                    "Can't move %s here - it would break: %s" % (feat.Label, msg), 5000)
            except Exception:
                pass
        self.refresh()

    def _reorder(self, feat, new_index):
        """Move `feat` to position `new_index` in the feature list, rewiring the
        PartDesign BaseFeature chain. Recomputes; if anything goes invalid the
        whole move is reverted. Returns (ok, message)."""
        body = self._body
        if body is None:
            return False, "no active body"
        doc = body.Document
        full = list(body.Group)                                  # may include Origin
        feats = [o for o in full if o.TypeId != 'App::Origin']
        if feat not in feats:
            return False, "feature not in body"
        new_index = max(0, min(new_index, len(feats) - 1))
        if feats.index(feat) == new_index:
            return True, "no change"
        # snapshot for revert
        snap_group = full
        snap_base = {f.Name: getattr(f, 'BaseFeature', None)
                     for f in feats if 'BaseFeature' in f.PropertiesList}
        snap_tip = body.Tip
        self._guard = True
        try:
            neworder = list(feats)
            neworder.remove(feat)
            neworder.insert(new_index, feat)
            origins = [o for o in full if o.TypeId == 'App::Origin']
            body.Group = origins + neworder
            prev = None
            for f in neworder:
                if _is_solid(f):
                    f.BaseFeature = prev
                    prev = f
            if prev is not None:
                body.Tip = prev
            doc.recompute()
            bad = [o.Label for o in doc.Objects if not o.isValid()]
            if bad:
                raise RuntimeError(", ".join(bad))
            App.Console.PrintMessage(
                "[PartDesignTimeline] moved %s\n" % feat.Label)
            return True, "ok"
        except Exception as e:
            # revert to the snapshot and recompute back to a valid state
            for name, base in snap_base.items():
                o = doc.getObject(name)
                if o is not None:
                    o.BaseFeature = base
            body.Group = snap_group
            body.Tip = snap_tip
            try:
                doc.recompute()
            except Exception:
                pass
            return False, str(e)
        finally:
            self._guard = False

    def eventFilter(self, obj, ev):
        et = ev.type()
        # --- chip: double-click edit, right-click menu, press/drag to reorder ---
        if hasattr(obj, '_ft_feat'):
            if et == QtCore.QEvent.MouseButtonDblClick:
                try:
                    Gui.activeDocument().setEdit(obj._ft_feat)
                except Exception as e:
                    App.Console.PrintWarning("[PartDesignTimeline] edit: %s\n" % e)
                return True
            if et == QtCore.QEvent.ContextMenu:
                self._context_menu(obj)
                return True
            if et == QtCore.QEvent.MouseButtonPress and ev.button() == QtCore.Qt.LeftButton:
                self._press_pos = _evt_pos(ev)
            elif et == QtCore.QEvent.MouseMove and (ev.buttons() & QtCore.Qt.LeftButton):
                if (self._press_pos is not None and
                        (_evt_pos(ev) - self._press_pos).manhattanLength()
                        >= QtWidgets.QApplication.startDragDistance()):
                    self._start_drag(obj)
                    return True   # consumed as a drag, not a click
            elif et == QtCore.QEvent.MouseButtonRelease:
                self._press_pos = None
        # --- strip: accept drops, draw the insertion line, perform the move ---
        if obj is self._row:
            if et in (QtCore.QEvent.DragEnter, QtCore.QEvent.DragMove):
                if ev.mimeData().hasFormat(_MIME):
                    ev.acceptProposedAction()
                    self._show_drop_indicator(_evt_pos(ev))
                    return True
            elif et == QtCore.QEvent.Drop:
                if ev.mimeData().hasFormat(_MIME):
                    self._do_drop(_evt_pos(ev))
                    ev.acceptProposedAction()
                    return True
            elif et == QtCore.QEvent.DragLeave:
                self._hide_drop_indicator()
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
