import sys, time, csv
from datetime import datetime
import serial
import requests
from PyQt6 import QtWidgets, QtCore
import pyqtgraph as pg

SERIAL_PORT = "COM10"
BAUD_RATE = 9600
THINGSPEAK_WRITE_API_KEY = "GD5CMCG22YIUZJEP"
THINGSPEAK_URL = "https://api.thingspeak.com/update"
UPLOAD_INTERVAL = 16
GRAPH_WINDOW_SECONDS = 120


class SerialWorker(QtCore.QThread):
    data = QtCore.pyqtSignal(float, float, float)
    message = QtCore.pyqtSignal(str)
    status = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.ser = None
        self.running = True

    def run(self):
        try:
            self.ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
            time.sleep(2)
            self.status.emit(f"Connected: {SERIAL_PORT}")
            while self.running:
                line = self.ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                self.message.emit(line)
                if line.startswith("DATA,"):
                    p = line.split(",")
                    if len(p) == 4:
                        try:
                            self.data.emit(float(p[1]), float(p[2]), float(p[3]))
                        except ValueError:
                            self.message.emit("Invalid DATA: " + line)
        except Exception as e:
            self.status.emit(f"Serial connection error: {e}")
        finally:
            if self.ser and self.ser.is_open:
                self.ser.close()

    def command(self, c):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(c.encode())
                self.ser.flush()
            except Exception as e:
                self.message.emit(f"Command error: {e}")

    def stop(self):
        self.running = False
        try:
            self.command("2")
        except Exception:
            pass
        self.wait(1500)
        if self.ser and self.ser.is_open:
            self.ser.close()


class TestBench(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KAZIAH AERORESEARCH - Vertical Test Rig")
        self.resize(1450, 900)

        self.testing = False
        self.t0 = None
        self.last_upload = 0
        self.f = self.p = self.t = 0.0
        self.max_f = self.max_p = 0.0
        self.max_t = -999.0
        self.x, self.fs, self.ps, self.ts = [], [], [], []
        self.rows = []

        self.build_ui()

        self.worker = SerialWorker()
        self.worker.data.connect(self.new_data)
        self.worker.message.connect(self.console_msg)
        self.worker.status.connect(self.set_status)
        self.worker.start()

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_plots)
        self.timer.start(100)

        self.ts_timer = QtCore.QTimer()
        self.ts_timer.timeout.connect(self.upload)
        self.ts_timer.start(1000)

    def build_ui(self):
        w = QtWidgets.QWidget()
        self.setCentralWidget(w)
        layout = QtWidgets.QVBoxLayout(w)

        title = QtWidgets.QLabel("KAZIAH AERORESEARCH — VERTICAL TEST RIG")
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size:24px;font-weight:bold;padding:10px")
        layout.addWidget(title)

        vals = QtWidgets.QHBoxLayout()
        self.force = self.card(vals, "FORCE", "0.00 N")
        self.pressure = self.card(vals, "PRESSURE", "0.000 MPa")
        self.temp = self.card(vals, "TEMPERATURE", "0.00 °C")
        self.maxforce = self.card(vals, "MAX FORCE", "0.00 N")
        self.maxtemp = self.card(vals, "MAX TEMP", "0.00 °C")
        layout.addLayout(vals)

        buttons = QtWidgets.QHBoxLayout()
        for text, fn in [
            ("START TEST", self.start),
            ("STOP TEST", self.stop),
            ("TARE LOAD CELL", self.tare),
            ("CLEAR GRAPH", self.clear),
            ("SAVE CSV", self.save)
        ]:
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            buttons.addWidget(b)
        layout.addLayout(buttons)

        self.status = QtWidgets.QLabel("Connecting...")
        layout.addWidget(self.status)

        plots = QtWidgets.QHBoxLayout()
        self.fp = self.plot("Force vs Time", "Force (N)")
        self.pp = self.plot("Pressure vs Time", "Pressure (MPa)")
        self.tp = self.plot("Temperature vs Time", "Temperature (°C)")
        plots.addWidget(self.fp)
        plots.addWidget(self.pp)
        plots.addWidget(self.tp)
        layout.addLayout(plots, 1)

        layout.addWidget(QtWidgets.QLabel("ARDUINO SERIAL MONITOR"))
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(140)
        layout.addWidget(self.console)

    def card(self, parent, title, value):
        box = QtWidgets.QGroupBox(title)
        lay = QtWidgets.QVBoxLayout(box)
        lab = QtWidgets.QLabel(value)
        lab.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        lab.setStyleSheet("font-size:21px;font-weight:bold;padding:8px")
        lay.addWidget(lab)
        parent.addWidget(box)
        return lab

    def plot(self, title, ylabel):
        p = pg.PlotWidget()
        p.setBackground("w")
        p.setTitle(title, color="k")
        p.setLabel("left", ylabel, color="k")
        p.setLabel("bottom", "Time (s)", color="k")
        p.showGrid(x=True, y=True, alpha=0.25)
        p.getAxis("left").setTextPen("k")
        p.getAxis("bottom").setTextPen("k")
        return p

    @QtCore.pyqtSlot(float, float, float)
    def new_data(self, f, p, t):
        self.f, self.p, self.t = f, p, t
        if self.t0 is None:
            self.t0 = time.time()
        elapsed = time.time() - self.t0

        self.x.append(elapsed)
        self.fs.append(f)
        self.ps.append(p)
        self.ts.append(t)

        cutoff = elapsed - GRAPH_WINDOW_SECONDS
        while self.x and self.x[0] < cutoff:
            self.x.pop(0); self.fs.pop(0); self.ps.pop(0); self.ts.pop(0)

        self.max_f = max(self.max_f, f)
        self.max_p = max(self.max_p, p)
        self.max_t = max(self.max_t, t)

        self.force.setText(f"{f:.2f} N")
        self.pressure.setText(f"{p:.3f} MPa")
        self.temp.setText(f"{t:.2f} °C")
        self.maxforce.setText(f"{self.max_f:.2f} N")
        self.maxtemp.setText(f"{self.max_t:.2f} °C")

        if self.testing:
            self.rows.append([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                elapsed, f, p, t
            ])

    def update_plots(self):
        if not self.x:
            return
        for p in (self.fp, self.pp, self.tp):
            p.clear()
        self.fp.plot(self.x, self.fs, pen=pg.mkPen(width=2))
        self.pp.plot(self.x, self.ps, pen=pg.mkPen(width=2))
        self.tp.plot(self.x, self.ts, pen=pg.mkPen(width=2))

    def start(self):
        self.testing = True
        self.t0 = time.time()
        self.max_f = self.max_p = 0.0
        self.max_t = -999.0
        self.rows.clear()
        self.worker.command("1")
        self.status.setText("TEST RUNNING")
        self.console_msg(">>> START TEST")

    def stop(self):
        self.testing = False
        self.worker.command("2")
        self.status.setText("TEST STOPPED")
        self.console_msg(">>> STOP TEST")

    def tare(self):
        if self.testing:
            self.console_msg("Stop the test before taring.")
        else:
            self.worker.command("4")
            self.console_msg(">>> TARE COMMAND SENT")

    def clear(self):
        self.x.clear(); self.fs.clear(); self.ps.clear(); self.ts.clear()
        self.fp.clear(); self.pp.clear(); self.tp.clear()
        self.console_msg(">>> GRAPH CLEARED")

    def save(self):
        if not self.rows:
            QtWidgets.QMessageBox.information(self, "No Data", "No test data has been recorded.")
            return
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Test Data",
            f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "CSV Files (*.csv)"
        )
        if fn:
            with open(fn, "w", newline="", encoding="utf-8") as file:
                out = csv.writer(file)
                out.writerow(["Timestamp", "Time_s", "Force_N", "Pressure_MPa", "Temperature_C"])
                out.writerows(self.rows)
            self.console_msg("CSV saved: " + fn)

    def upload(self):
        if not self.testing or THINGSPEAK_WRITE_API_KEY.startswith("PASTE_"):
            return
        now = time.time()
        if now - self.last_upload < UPLOAD_INTERVAL:
            return
        try:
            r = requests.get(
                THINGSPEAK_URL,
                params={
                    "api_key": THINGSPEAK_WRITE_API_KEY,
                    "field1": self.f,
                    "field2": self.p,
                    "field3": self.t
                },
                timeout=5
            )
            if r.text != "0":
                self.last_upload = now
                self.console_msg("ThingSpeak upload SUCCESS - Entry " + r.text)
            else:
                self.console_msg("ThingSpeak upload FAILED")
        except requests.RequestException as e:
            self.console_msg("ThingSpeak error: " + str(e))

    def console_msg(self, msg):
        self.console.appendPlainText(msg)
        if self.console.document().blockCount() > 300:
            self.console.removeExtraSelections()

    def set_status(self, msg):
        self.status.setText(msg)
        self.console_msg(msg)

    def closeEvent(self, event):
        self.worker.stop()
        event.accept()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = TestBench()
    win.show()
    sys.exit(app.exec())
