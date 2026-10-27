"""Look « radio » (essai) : boutons métal en relief, cases en interrupteurs à
levier avec voyant, champs et listes en afficheurs verts façon écran LCD,
curseurs à piste ambre et bouton chromé.

S'applique à toute l'appli (ic7300_scope.py --look-radio). Les flèches et les
interrupteurs sont dessinés au démarrage en petites images dans
~/.cache/ic7300-scope : la feuille de style Qt ne sait pas les dessiner seule.
"""
import os

from PyQt5 import QtCore, QtGui

STYLE = """
QPushButton {
    color: #e0e6eb; font-weight: bold; padding: 2px 6px; border-radius: 5px;
    border: 1px solid #5a6672; border-bottom: 2px solid #0b0f13;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4a5560, stop:0.5 #2c343c, stop:1 #1a2026);
}
QPushButton:hover { border-color: #8fa0b0; }
QPushButton:pressed, QPushButton:checked {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #151a1f, stop:1 #2c343c);
    border-top: 2px solid #0b0f13; border-bottom: 1px solid #5a6672;
}
QPushButton:disabled { color: #4f5b66; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background: #050807; color: #7cfc9a;
    border: 2px solid #2a3138; border-top-color: #0b0f13; border-left-color: #0b0f13;
    border-radius: 4px; padding: 1px 3px; selection-background-color: #1b5e20;
}
QComboBox QAbstractItemView { background: #050807; color: #7cfc9a; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled { color: #2e5236; }
QSpinBox, QDoubleSpinBox { padding-right: 18px; }
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {
    width: 16px; border: 1px solid #0b0f13;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #5a6672, stop:1 #2c343c);
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border; subcontrol-position: top right; border-top-right-radius: 3px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border; subcontrol-position: bottom right; border-bottom-right-radius: 3px;
}
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed { background: #151a1f; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url(%(up)s); width: 10px; height: 7px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow,
QComboBox::down-arrow { image: url(%(down)s); width: 10px; height: 7px; }
QComboBox::drop-down {
    width: 18px; border-left: 1px solid #0b0f13; border-top-right-radius: 3px; border-bottom-right-radius: 3px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #5a6672, stop:1 #2c343c);
}
QCheckBox { spacing: 4px; }
QCheckBox::indicator { width: 40px; height: 16px; }
QCheckBox::indicator:unchecked { image: url(%(off)s); }
QCheckBox::indicator:checked { image: url(%(on)s); }
QCheckBox::indicator:disabled { image: url(%(off_disabled)s); }
QSlider:horizontal { min-height: 28px; }
QSlider::groove:horizontal {
    height: 6px; border-radius: 3px; background: #050807; border: 1px solid #2a3138;
}
QSlider::sub-page:horizontal { border-radius: 3px; background: #ffb300; }
QSlider::handle:horizontal {
    width: 18px; height: 26px; margin: -11px 0; border-radius: 5px; border: 1px solid #0b0f13;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8a96a2, stop:0.5 #dfe6ec, stop:1 #6b7682);
}
QSlider::sub-page:horizontal:disabled { background: #6b5a2a; }
QGroupBox {
    border: 1px solid #2a3138; border-radius: 6px; margin-top: 10px; padding-top: 6px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #161c22, stop:1 #101418);
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #ffb300; font-weight: bold; }
"""


def _arrow(points):
    image = QtGui.QImage(10, 7, QtGui.QImage.Format_ARGB32)
    image.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(image)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor("#e0e6eb"))
    p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(*xy) for xy in points]))
    p.end()
    return image


def _switch(on, enabled=True):
    """Voyant (vert allumé avec halo) puis interrupteur à levier, 40 × 16."""
    image = QtGui.QImage(40, 16, QtGui.QImage.Format_ARGB32)
    image.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(image)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    lit = on and enabled
    led = QtCore.QRectF(2, 3.5, 9, 9)
    if lit:
        glow = QtGui.QRadialGradient(led.center(), 7)
        glow.setColorAt(0, QtGui.QColor(124, 252, 154, 150))
        glow.setColorAt(1, QtGui.QColor(124, 252, 154, 0))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(led.adjusted(-2, -2, 2, 2))
    grad = QtGui.QRadialGradient(led.center() - QtCore.QPointF(1.5, 1.5), 9)
    grad.setColorAt(0, QtGui.QColor("#d0ffd8" if lit else "#5a6672"))
    grad.setColorAt(1, QtGui.QColor("#1faa45" if lit else "#1a2026"))
    p.setPen(QtGui.QPen(QtGui.QColor("#0b0f13"), 1))
    p.setBrush(grad)
    p.drawEllipse(led)
    track = QtCore.QRectF(14, 1, 25, 14)
    p.setBrush(QtGui.QColor("#050807"))
    p.setPen(QtGui.QPen(QtGui.QColor("#2a3138"), 1))
    p.drawRoundedRect(track, 7, 7)
    knob = QtCore.QRectF(0, 0, 11, 11)
    knob.moveCenter(QtCore.QPointF(track.right() - 7 if on else track.left() + 7, track.center().y()))
    metal = QtGui.QLinearGradient(knob.topLeft(), knob.bottomLeft())
    metal.setColorAt(0, QtGui.QColor("#eef2f5" if enabled else "#6b7682"))
    metal.setColorAt(1, QtGui.QColor("#6b7682" if enabled else "#3a444f"))
    p.setBrush(metal)
    p.setPen(QtGui.QPen(QtGui.QColor("#0b0f13"), 1))
    p.drawEllipse(knob)
    p.end()
    return image


def style():
    """Feuille de style, avec ses images (créées si besoin)."""
    folder = os.path.join(os.path.expanduser("~/.cache"), "ic7300-scope")
    os.makedirs(folder, exist_ok=True)
    images = {"up": _arrow(((0, 7), (5, 0), (10, 7))), "down": _arrow(((0, 0), (10, 0), (5, 7))),
              "on": _switch(True), "off": _switch(False), "off_disabled": _switch(False, False)}
    paths = {}
    for name, image in images.items():
        paths[name] = os.path.join(folder, f"look-{name}.png")
        image.save(paths[name])
    return STYLE % paths
