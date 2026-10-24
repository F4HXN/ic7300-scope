"""Fenêtre des mémoires : les fréquences qu'on utilise souvent.

Onglet PC : les mémoires de l'appli, enregistrées dans memoires.json ; les
cases se modifient directement. Onglet Radio : les canaux 1 à 99 de
l'IC-7300, lus au démarrage. Double-clic (ou Aller) règle la radio sur la
mémoire, mode compris.
"""
import json
import os

from PyQt5 import QtCore, QtWidgets

MEMORY_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME")
                           or os.path.expanduser("~/.config"),
                           "ic7300-scope", "memoires.json")

# mémoires de départ : nom, fréquence (Hz), mode, note
DEFAULT_MEMORIES = [
    ("FT8 160 m", 1_840_000, "USB-D", ""),
    ("FT8 80 m", 3_573_000, "USB-D", ""),
    ("FT8 40 m", 7_074_000, "USB-D", ""),
    ("FT8 30 m", 10_136_000, "USB-D", ""),
    ("FT8 20 m", 14_074_000, "USB-D", ""),
    ("FT8 17 m", 18_100_000, "USB-D", ""),
    ("FT8 15 m", 21_074_000, "USB-D", ""),
    ("FT8 12 m", 24_915_000, "USB-D", ""),
    ("FT8 10 m", 28_074_000, "USB-D", ""),
    ("FT8 6 m", 50_313_000, "USB-D", ""),
    ("FT4 20 m", 14_080_000, "USB-D", ""),
    ("Balises NCDXF 20 m", 14_100_000, "CW", "Une balise toutes les 10 s"),
    ("Balises NCDXF 17 m", 18_110_000, "CW", ""),
    ("Balises NCDXF 15 m", 21_150_000, "CW", ""),
    ("Balises NCDXF 12 m", 24_930_000, "CW", ""),
    ("Balises NCDXF 10 m", 28_200_000, "CW", ""),
    ("QRP CW 40 m", 7_030_000, "CW", ""),
    ("QRP CW 20 m", 14_060_000, "CW", ""),
    ("SSTV 80 m", 3_735_000, "LSB", "3730-3740 kHz"),
    ("SSTV 40 m", 7_165_000, "LSB", ""),
    ("SSTV 20 m", 14_230_000, "USB", "La plus active"),
    ("SSTV 15 m", 21_340_000, "USB", ""),
    ("SSTV 10 m", 28_680_000, "USB", ""),
    ("Fax météo DWD", 7_878_100, "USB", "7880 kHz, de jour"),
    ("Fax météo DWD", 3_853_100, "USB", "3855 kHz, le soir"),
    ("Fax météo DWD", 13_880_600, "USB", "13882,5 kHz"),
    ("RTTY 80 m", 3_590_000, "LSB-D", "45 bauds, 3580-3600 kHz"),
    ("RTTY 40 m", 7_040_000, "LSB-D", "7040-7050 kHz"),
    ("RTTY 30 m", 10_142_000, "LSB-D", "10140-10150 kHz"),
    ("RTTY 20 m", 14_085_000, "LSB-D", "14080-14099 kHz"),
    ("RTTY 17 m", 18_102_000, "LSB-D", "18100-18109 kHz"),
    ("RTTY 15 m", 21_085_000, "LSB-D", "21080-21120 kHz"),
    ("RTTY 12 m", 24_920_000, "LSB-D", "24915-24929 kHz"),
    ("RTTY 10 m", 28_085_000, "LSB-D", "28080-28150 kHz"),
    ("RTTY météo DWD", 10_100_800, "USB", "50 bauds, écart 450 Hz"),
]
COLUMNS = ["Nom", "Fréquence (MHz)", "Mode", "Note"]


def mhz(hz):
    """14074000 → « 14.074 », 7012345 → « 7.012345 » (au moins 3 décimales)."""
    text = f"{hz / 1e6:.6f}"
    while text.endswith("0") and len(text.split(".")[1]) > 3:
        text = text[:-1]
    return text


def load_memories():
    try:
        with open(MEMORY_FILE) as f:
            return [tuple(m) for m in json.load(f)]
    except (OSError, ValueError):
        return list(DEFAULT_MEMORIES)


def save_memories(memories):
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    with open(MEMORY_FILE, "w") as f:
        json.dump(memories, f, ensure_ascii=False, indent=1)


class MemoryWindow(QtWidgets.QWidget):
    def __init__(self, main, modes, parse_frequency):
        # fenêtre fille : elle reste devant la fenêtre principale
        super().__init__(main, QtCore.Qt.Window)
        self.main = main
        self.modes = modes
        self.parse_frequency = parse_frequency
        self.setWindowTitle("Mémoires — IC-7300 Scope")
        self.resize(720, 560)
        outer = QtWidgets.QVBoxLayout(self)

        # saisie à la main d'une nouvelle mémoire, pour le PC ou la radio
        entry = QtWidgets.QGroupBox("Nouvelle mémoire")
        entry_row = QtWidgets.QHBoxLayout(entry)
        self.new_name = QtWidgets.QLineEdit()
        self.new_name.setPlaceholderText("Nom")
        self.new_freq = QtWidgets.QLineEdit()
        self.new_freq.setPlaceholderText("Fréquence")
        self.new_freq.setFixedWidth(110)
        self.new_freq.setToolTip("En MHz (14.074) ou en kHz (14074)")
        self.new_mode = QtWidgets.QComboBox()
        self.new_mode.addItems(modes)
        self.new_mode.setCurrentText("USB")
        self.new_channel = QtWidgets.QSpinBox()
        self.new_channel.setRange(1, 99)
        self.new_channel.setPrefix("Canal ")
        new_pc = QtWidgets.QPushButton("Ajouter au PC")
        new_pc.clicked.connect(self.new_to_pc)
        new_radio = QtWidgets.QPushButton("Écrire dans la radio")
        new_radio.setToolTip("Écrit dans ce canal de l'IC-7300 (le canal est remplacé)")
        new_radio.clicked.connect(self.new_to_radio)
        entry_row.addWidget(self.new_name, stretch=1)
        entry_row.addWidget(self.new_freq)
        entry_row.addWidget(self.new_mode)
        entry_row.addSpacing(10)
        entry_row.addWidget(new_pc)
        entry_row.addSpacing(10)
        entry_row.addWidget(self.new_channel)
        entry_row.addWidget(new_radio)
        outer.addWidget(entry)

        self.tabs = QtWidgets.QTabWidget()
        outer.addWidget(self.tabs)
        pc_tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(pc_tab)

        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditKeyPressed
                                   | QtWidgets.QAbstractItemView.SelectedClicked)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        self.table.setStyleSheet("font: 11pt;")
        self.table.cellDoubleClicked.connect(lambda row, _: self.tune(row))
        self.table.itemChanged.connect(self.on_edited)

        add = QtWidgets.QPushButton("Mémoriser la fréquence actuelle")
        add.clicked.connect(self.add_current)
        go = QtWidgets.QPushButton("Aller")
        go.clicked.connect(lambda: self.tune(self.table.currentRow()))
        up = QtWidgets.QPushButton("▲")
        up.setToolTip("Monter la mémoire")
        up.clicked.connect(lambda: self.move(-1))
        down = QtWidgets.QPushButton("▼")
        down.setToolTip("Descendre la mémoire")
        down.clicked.connect(lambda: self.move(1))
        # copie dans un canal mémoire de la radio (utilisable sans le PC)
        self.channel = QtWidgets.QSpinBox()
        self.channel.setRange(1, 99)
        self.channel.setPrefix("Canal ")
        self.channel.setToolTip("Canal mémoire de l'IC-7300 (1 à 99)")
        to_radio = QtWidgets.QPushButton("Envoyer dans la radio")
        to_radio.setToolTip("Écrit la mémoire choisie dans ce canal de l'IC-7300 "
                            "(le canal est remplacé)")
        to_radio.clicked.connect(self.send_to_radio)
        all_to_radio = QtWidgets.QPushButton("Tout envoyer (canaux libres)")
        all_to_radio.setToolTip("Range dans les canaux vides de la radio toutes les "
                                "mémoires qu'elle n'a pas encore. Aucun canal "
                                "rempli n'est touché.")
        all_to_radio.clicked.connect(self.send_all_to_radio)
        delete = QtWidgets.QPushButton("Supprimer")
        delete.clicked.connect(self.delete)
        buttons = QtWidgets.QHBoxLayout()
        for button in (add, go, up, down):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.channel)
        buttons.addWidget(to_radio)
        buttons.addSpacing(20)
        buttons.addWidget(delete)
        self.info = QtWidgets.QLabel("Double-clic : aller sur la mémoire. "
                                     "Clic sur une case déjà choisie : la modifier.")
        self.info.setStyleSheet("color: #90a4ae;")

        layout.addWidget(self.table, stretch=1)
        layout.addLayout(buttons)
        all_row = QtWidgets.QHBoxLayout()
        all_row.addStretch()
        all_row.addWidget(all_to_radio)
        layout.addLayout(all_row)
        layout.addWidget(self.info)
        self.tabs.addTab(pc_tab, "Mémoires du PC")
        self.memories = load_memories()
        self._fill()

        # canaux de la radio, en lecture
        radio_tab = QtWidgets.QWidget()
        radio_layout = QtWidgets.QVBoxLayout(radio_tab)
        self.radio_table = QtWidgets.QTableWidget(0, 4)
        self.radio_table.setHorizontalHeaderLabels(["Canal", "Nom", "Fréquence (MHz)", "Mode"])
        self.radio_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        # plusieurs canaux à la fois (Ctrl ou Maj + clic) pour les vider
        self.radio_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.radio_table.setEditTriggers(QtWidgets.QAbstractItemView.EditKeyPressed
                                         | QtWidgets.QAbstractItemView.SelectedClicked)
        self.radio_table.verticalHeader().setVisible(False)
        radio_header = self.radio_table.horizontalHeader()
        for col in range(3):
            radio_header.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeToContents)
        radio_header.setStretchLastSection(True)
        self.radio_table.setStyleSheet("font: 11pt;")
        self.radio_table.cellDoubleClicked.connect(lambda row, _: self.tune_radio(row))
        self.radio_table.itemChanged.connect(self.on_radio_edited)
        radio_go = QtWidgets.QPushButton("Aller")
        radio_go.clicked.connect(lambda: self.tune_radio(self.radio_table.currentRow()))
        copy = QtWidgets.QPushButton("Copier dans le PC")
        copy.setToolTip("Ajoute ce canal aux mémoires du PC")
        copy.clicked.connect(self.copy_from_radio)
        clear = QtWidgets.QPushButton("Vider")
        clear.setToolTip("Efface de la radio les canaux choisis "
                         "(Ctrl ou Maj + clic pour en choisir plusieurs)")
        clear.clicked.connect(self.clear_radio_channel)
        clear_all = QtWidgets.QPushButton("Tout vider")
        clear_all.setToolTip("Efface tous les canaux mémoire de la radio")
        clear_all.clicked.connect(lambda: self.clear_radio_channel(everything=True))
        self.show_empty = QtWidgets.QCheckBox("Canaux vides")
        self.show_empty.setToolTip("Montrer aussi les canaux vides, pour en remplir un")
        self.show_empty.toggled.connect(self.show_radio)
        reread = QtWidgets.QPushButton("Relire la radio")
        reread.clicked.connect(self.reread_radio)
        radio_buttons = QtWidgets.QHBoxLayout()
        for button in (radio_go, copy, clear, clear_all, self.show_empty):
            radio_buttons.addWidget(button)
        radio_buttons.addStretch()
        radio_buttons.addWidget(reread)
        self.radio_info = QtWidgets.QLabel("")
        self.radio_info.setWordWrap(True)
        self.radio_info.setStyleSheet("color: #90a4ae;")
        radio_layout.addWidget(self.radio_table, stretch=1)
        radio_layout.addLayout(radio_buttons)
        radio_layout.addWidget(self.radio_info)
        self.tabs.addTab(radio_tab, "Mémoires de la radio")
        self.show_radio()

    # -- tableau

    def _fill(self, select=None):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.memories))
        for row, (name, hz, mode, note) in enumerate(self.memories):
            for col, text in enumerate((name, mhz(hz),
                                        mode, note)):
                item = QtWidgets.QTableWidgetItem(text)
                if col == 1:
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        if select is not None:
            self.table.selectRow(select)

    def on_edited(self, item):
        row, col = item.row(), item.column()
        name, hz, mode, note = self.memories[row]
        text = item.text().strip()
        if col == 0:
            name = text
        elif col == 1:
            value = self.parse_frequency(text)
            if value is None:
                self.info.setText("Fréquence non comprise (ex. 14.074 ou 7074)")
            else:
                hz = value
        elif col == 2:
            if text.upper() in self.modes:
                mode = text.upper()
            else:
                self.info.setText("Modes possibles : " + ", ".join(self.modes))
        else:
            note = text
        self.memories[row] = (name, hz, mode, note)
        save_memories(self.memories)
        self._fill(row)

    def add_current(self):
        hz = self.main.freq_hz
        if not hz:
            self.info.setText("Fréquence de la radio inconnue")
            return
        mode = self.main.mode_combo.currentText()
        self.memories.append((f"{hz / 1e6:.3f} {mode}", hz, mode, ""))
        save_memories(self.memories)
        row = len(self.memories) - 1
        self._fill(row)
        self.table.scrollToBottom()
        self.info.setText("Mémorisée : clic sur son nom pour le changer")

    def move(self, step):
        row = self.table.currentRow()
        other = row + step
        if row < 0 or not 0 <= other < len(self.memories):
            return
        self.memories[row], self.memories[other] = self.memories[other], self.memories[row]
        save_memories(self.memories)
        self._fill(other)

    def delete(self):
        row = self.table.currentRow()
        if row < 0:
            return
        name = self.memories[row][0]
        answer = QtWidgets.QMessageBox.question(
            self, "Supprimer", f"Supprimer la mémoire « {name} » ?")
        if answer != QtWidgets.QMessageBox.Yes:
            return
        del self.memories[row]
        save_memories(self.memories)
        self._fill(min(row, len(self.memories) - 1) if self.memories else None)

    def send_to_radio(self):
        row = self.table.currentRow()
        if row < 0:
            self.info.setText("Choisir d'abord une mémoire dans la liste")
            return
        civ = self.main.civ
        if not civ or self.main.tx_active:
            self.info.setText("Radio non pilotée ou en émission : envoi impossible")
            return
        name, hz, mode, _ = self.memories[row]
        channel = self.channel.value()
        answer = QtWidgets.QMessageBox.question(
            self, "Envoyer dans la radio",
            f"Écrire « {name} » ({mhz(hz)} MHz {mode}) dans le canal {channel} "
            f"de l'IC-7300 ?\n\nCe que contient ce canal sera remplacé.")
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.info.setText(f"Écriture du canal {channel}…")
        civ.write_memory(channel, hz, mode, name)
        if self.channel.value() < 99:
            self.channel.setValue(channel + 1)     # canal suivant pour la prochaine

    def send_all_to_radio(self):
        main = self.main
        if not main.civ or main.tx_active:
            self.info.setText("Radio non pilotée ou en émission : envoi impossible")
            return
        if not main.radio_memories_ready:
            self.info.setText("Attendre la fin de la lecture des mémoires de la radio "
                              f"({main.radio_memory_status})")
            return
        already = {(hz, mode) for _, hz, mode in main.radio_memories.values()}
        todo = [m for m in self.memories if (m[1], m[2]) not in already]
        used = set(main.radio_memories) | main.radio_memory_odd
        free = [c for c in range(1, 100) if c not in used]
        if not todo:
            self.info.setText("La radio a déjà toutes ces mémoires")
            return
        count = min(len(todo), len(free))
        if not count:
            self.info.setText("Plus aucun canal libre dans la radio")
            return
        pairs = list(zip(free, todo))
        answer = QtWidgets.QMessageBox.question(
            self, "Tout envoyer dans la radio",
            f"Ranger {count} mémoires dans les canaux libres "
            f"{pairs[0][0]} à {pairs[-1][0]} de l'IC-7300 ?\n\n"
            "Les canaux déjà remplis ne sont pas touchés."
            + (f"\n{len(todo) - count} mémoires ne trouveront pas de place."
               if len(todo) > count else ""))
        if answer != QtWidgets.QMessageBox.Yes:
            return
        for channel, (name, hz, mode, _) in pairs:
            main.civ.write_memory(channel, hz, mode, name)
        self.info.setText(f"Écriture de {count} canaux en cours…")
        self.tabs.setTabText(1, "Mémoires de la radio (écriture…)")

    def on_memory_written(self, ok, message):
        text = ("✔ " if ok else "✘ ") + message
        self.info.setText(text)
        self.radio_info.setText(text)

    # -- saisie à la main

    def _new_entry(self):
        """(nom, Hz, mode) de la saisie, None (avec message) si la fréquence manque."""
        hz = self.parse_frequency(self.new_freq.text())
        if hz is None:
            self.info.setText("Fréquence non comprise (ex. 14.074 ou 7074)")
            self.radio_info.setText(self.info.text())
            self.new_freq.setFocus()
            return None
        mode = self.new_mode.currentText()
        name = self.new_name.text().strip() or f"{mhz(hz)} {mode}"
        return name, hz, mode

    def new_to_pc(self):
        entry = self._new_entry()
        if entry is None:
            return
        name, hz, mode = entry
        self.memories.append((name, hz, mode, ""))
        save_memories(self.memories)
        self._fill(len(self.memories) - 1)
        self.table.scrollToBottom()
        self.tabs.setCurrentIndex(0)
        self.info.setText(f"« {name} » ajoutée aux mémoires du PC")

    def new_to_radio(self):
        entry = self._new_entry()
        if entry is None:
            return
        name, hz, mode = entry
        civ = self.main.civ
        self.tabs.setCurrentIndex(1)
        if not civ or self.main.tx_active:
            self.radio_info.setText("Radio non pilotée ou en émission : envoi impossible")
            return
        channel = self.new_channel.value()
        old = self.main.radio_memories.get(channel)
        question = f"Écrire « {name} » ({mhz(hz)} MHz {mode}) dans le canal {channel} ?"
        if old:
            question += (f"\n\nLe canal contient déjà « {old[0] or 'sans nom'} » "
                         f"({mhz(old[1])} MHz {old[2]}), qui sera remplacé.")
        answer = QtWidgets.QMessageBox.question(self, "Écrire dans la radio", question)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.radio_info.setText(f"Écriture du canal {channel}…")
        civ.write_memory(channel, hz, mode, name)

    # -- canaux de la radio

    def show_radio(self, *_):
        """Affiche les canaux lus (main.radio_memories : canal → nom, Hz, mode).

        Nom, fréquence et mode se modifient dans le tableau : le canal est
        aussitôt réécrit dans la radio."""
        memories = self.main.radio_memories
        current = self._radio_channel(self.radio_table.currentRow())
        channels = range(1, 100) if self.show_empty.isChecked() else sorted(memories)
        self.radio_table.blockSignals(True)
        self.radio_table.setRowCount(len(channels))
        for row, channel in enumerate(channels):
            name, hz, mode = memories.get(channel, ("", None, ""))
            odd = channel in self.main.radio_memory_odd
            texts = (str(channel), "(illisible)" if odd else name,
                     mhz(hz) if hz else "", mode)
            for col, text in enumerate(texts):
                item = QtWidgets.QTableWidgetItem(text)
                item.setData(QtCore.Qt.UserRole, channel)
                flags = item.flags()
                if col == 0 or odd:
                    flags &= ~QtCore.Qt.ItemIsEditable
                item.setFlags(flags)
                if col in (0, 2):
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.radio_table.setItem(row, col, item)
            if channel == current:
                self.radio_table.selectRow(row)
        self.radio_table.blockSignals(False)
        self.tabs.setTabText(1, f"Mémoires de la radio ({len(memories)})")
        # la saisie et l'onglet PC proposent le premier canal libre
        used = set(memories) | self.main.radio_memory_odd
        free = [c for c in range(1, 100) if c not in used]
        for box in (self.new_channel, self.channel):
            if free and box.value() in used and not box.hasFocus():
                box.setValue(free[0])
        self.radio_info.setText(self.main.radio_memory_status)
        if self.main.radio_memories_ready and self.info.text().startswith("Attendre"):
            self.info.setText("Lecture de la radio terminée : l'envoi est possible")

    def _radio_channel(self, row):
        item = self.radio_table.item(row, 0) if row >= 0 else None
        return item.data(QtCore.Qt.UserRole) if item else None

    def _radio_entry(self, row):
        channel = self._radio_channel(row)
        if channel not in self.main.radio_memories:
            return None
        return channel, *self.main.radio_memories[channel]

    def on_radio_edited(self, item):
        """Nom, fréquence ou mode modifié : le canal est réécrit dans la radio."""
        channel = item.data(QtCore.Qt.UserRole)
        name, hz, mode = self.main.radio_memories.get(channel, ("", None, "USB"))
        text = item.text().strip()
        col = item.column()

        def refuse(message):
            self.show_radio()           # remet le tableau comme avant
            self.radio_info.setText(message)

        if col == 1:
            name = text
        elif col == 2:
            hz = self.parse_frequency(text)
            if hz is None:
                return refuse("Fréquence non comprise (ex. 14.074 ou 7074)")
        elif col == 3:
            if text.upper() not in self.modes:
                return refuse("Modes possibles : " + ", ".join(self.modes))
            mode = text.upper()
        if hz is None:
            return refuse("Indiquer d'abord la fréquence de ce canal")
        if not mode:
            mode = "USB"
        civ = self.main.civ
        if not civ or self.main.tx_active:
            return refuse("Radio non pilotée ou en émission : canal non modifié")
        self.main.radio_memories[channel] = (name, hz, mode)     # affiché tout de suite
        self.show_radio()
        self.radio_info.setText(f"Écriture du canal {channel}…")
        civ.write_memory(channel, hz, mode, name)

    def clear_radio_channel(self, everything=False):
        memories = self.main.radio_memories
        if everything:
            channels = sorted(memories)
        else:
            rows = {index.row() for index in self.radio_table.selectedIndexes()}
            channels = sorted(c for c in map(self._radio_channel, rows) if c in memories)
        if not channels:
            self.radio_info.setText("Aucun canal rempli de choisi"
                                    if not everything else "La radio n'a aucune mémoire")
            return
        civ = self.main.civ
        if not civ or self.main.tx_active:
            self.radio_info.setText("Radio non pilotée ou en émission : rien n'est effacé")
            return
        if len(channels) == 1:
            name, hz, mode = memories[channels[0]]
            question = (f"Vider le canal {channels[0]} de l'IC-7300 "
                        f"({name or 'sans nom'}, {mhz(hz)} MHz {mode}) ?")
        else:
            question = (f"Vider {len(channels)} canaux de l'IC-7300 "
                        f"({', '.join(map(str, channels))}) ?\n\n"
                        "Ils seront perdus, sauf s'ils sont copiés dans le PC.")
        answer = QtWidgets.QMessageBox.question(
            self, "Tout vider" if everything else "Vider", question)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        for channel in channels:
            civ.clear_memory(channel)
        self.radio_info.setText(f"Effacement de {len(channels)} canal(aux)…")

    def tune_radio(self, row):
        entry = self._radio_entry(row)
        if entry is None:
            return
        channel, name, hz, mode = entry
        if self.main.tune_with_mode(hz, mode):
            self.radio_info.setText(f"Radio sur le canal {channel} : {mhz(hz)} MHz {mode}")
        else:
            self.radio_info.setText("Radio non pilotée ou en émission : réglage impossible")

    def copy_from_radio(self):
        entry = self._radio_entry(self.radio_table.currentRow())
        if entry is None:
            return
        channel, name, hz, mode = entry
        self.memories.append((name or f"Canal {channel}", hz, mode, f"Canal {channel} de la radio"))
        save_memories(self.memories)
        self._fill(len(self.memories) - 1)
        self.radio_info.setText(f"Canal {channel} copié dans les mémoires du PC")

    def reread_radio(self):
        if not self.main.civ:
            self.radio_info.setText("Radio non pilotée")
            return
        self.main.radio_memories.clear()
        self.main.radio_memory_odd.clear()
        self.main.radio_memories_ready = False
        self.main.civ.read_memories()
        self.show_radio()

    def tune(self, row):
        if not 0 <= row < len(self.memories):
            return
        name, hz, mode, _ = self.memories[row]
        if self.main.tune_with_mode(hz, mode):
            self.info.setText(f"Radio sur {name} : {mhz(hz)} MHz {mode}")
        else:
            self.info.setText("Radio non pilotée ou en émission : réglage impossible")
