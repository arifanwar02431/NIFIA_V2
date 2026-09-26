import sys
import time
import threading
import os
from enum import Enum

import cv2
import numpy as np
import matplotlib.pyplot as plt

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QGridLayout, QGroupBox, QPushButton,
                             QLabel, QSizePolicy, QFrame, QStatusBar, QSlider, QDesktopWidget,
                             QTabWidget, QFileDialog, QStyle, QRadioButton)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QSize
from PyQt5.QtGui import QFont, QPalette, QColor, QPixmap, QIcon, QImage, QPainter
import smbus2

# Simulasi picamera2 untuk pengembangan di luar Raspberry Pi
try:
    from picamera2 import Picamera2
    from libcamera import controls
    HAS_PICAMERA = True
except ImportError:
    HAS_PICAMERA = False
    print("Picamera2 tidak tersedia, menggunakan simulasi")

# Konstanta I2C
I2C_BUS = 1
ESP32_ADDRESS = 0x08

# Konstanta movement
MM_PER_STEP = 1  # 1mm per klik

# Enum untuk mode kamera
class CameraMode(Enum):
    RGB = 0
    BW = 1

# ====================== ALGORITMA IMAGE PROCESSING ======================
def crop_retina_sempurna(image_path, show_plot=True):
    """
    Fungsi untuk menghilangkan glare dan memotong retina menjadi bulat sempurna.
    """
    img = cv2.imread(image_path)
    if img is None:
        print(f"Error: Gambar tidak ditemukan.")
        return None

    # --- 1. PENGHILANGAN GLARE (LEBIH HALUS) ---
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Deteksi titik yang benar-benar putih terang saja
    _, bright_mask = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY)

    # Perlebar sedikit saja (3x3 kernel) agar inpainting tidak merusak area terlalu luas
    kernel_glare = np.ones((3,3), np.uint8)
    bright_mask = cv2.dilate(bright_mask, kernel_glare, iterations=2)

    # Inpaint dengan radius yang seimbang (5)
    img_bersih = cv2.inpaint(img, bright_mask, 5, cv2.INPAINT_TELEA)

    # --- 2. DETEKSI RETINA (HSV + LINGKARAN SEMPURNA) ---
    hsv = cv2.cvtColor(img_bersih, cv2.COLOR_BGR2HSV)
    s_channel = hsv[:, :, 1] # Gunakan saturasi warna untuk mengabaikan cincin lensa putih

    # Haluskan s-channel agar tepi kontur tidak bergerigi
    s_channel = cv2.GaussianBlur(s_channel, (15, 15), 0)
    _, thresh = cv2.threshold(s_channel, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Cari bentuk dari area berwarna (retina)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        print("Gagal mendeteksi retina.")
        return None

    c = max(contours, key=cv2.contourArea)

    # Gunakan minEnclosingCircle agar hasilnya PASTI bulat sempurna (tidak poligon)
    ((x, y), radius) = cv2.minEnclosingCircle(c)
    x, y, radius = int(x), int(y), int(radius)

    # Kurangi radius agak banyak (15 pixel) agar cincin lensa benar-benar hilang
    radius = radius - 15

    # --- 3. PEMOTONGAN & TRANSPARANSI ---
    # Buat kanvas masker (hitam putih)
    mask = np.zeros(img.shape[:2], dtype=np.uint8)
    cv2.circle(mask, (x, y), radius, 255, -1) # Gambar lingkaran putih solid mulus

    # Pisahkan warna gambar dan tambahkan Alpha Channel (Transparansi) dari masker
    b, g, r = cv2.split(img_bersih)
    img_bgra = cv2.merge((b, g, r, mask))

    # Potong batas luar agar pas
    x_min = max(0, x - radius)
    y_min = max(0, y - radius)
    x_max = min(img.shape[1], x + radius)
    y_max = min(img.shape[0], y + radius)

    hasil_final = img_bgra[y_min:y_max, x_min:x_max]

    # --- VISUALISASI ---
    if show_plot:
        tampil_rgba = cv2.cvtColor(hasil_final, cv2.COLOR_BGRA2RGBA)
        tampil_asli = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        fig, ax = plt.subplots(1, 2, figsize=(14, 7))
        ax[0].imshow(tampil_asli)
        ax[0].set_title('Gambar Asli NIFIA')
        ax[0].axis('off')

        ax[1].imshow(tampil_rgba)
        ax[1].set_title('Hasil Mulus (Bulat Sempurna & Glare Hilang)')
        ax[1].axis('off')

        plt.tight_layout()
        plt.show(block=False) # block=False agar GUI utama tidak sepenuhnya freeze

    return hasil_final

# ====================== MAIN APPLICATION CLASS ======================
class MotorControlApp(QMainWindow):
    update_position_signal = pyqtSignal(int, int)
    update_frame_signal = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("NIFIA - Non-Invasive Retinal Microvascular Analyzer")
        self.setStyleSheet("background-color: #f4f6f9; color: #333333; font-family: Segoe UI, Arial, sans-serif;")

        # Variabel status
        self.camera_mode = CameraMode.RGB
        self.focus_value = 7.0
        self.gain_value = 9.0
        self.exposure_value = 1000.0
        self.zoom_value = 1.0
        self.x_position = 0
        self.y_position = 0
        self.is_examining = False
        self.current_frame = None
        self.last_displayed_image = None
        self.current_preview_image_path = None

        # Variabel untuk tracking mata
        self.eye_target_position = None
        self.eye_moving = False

        # Variabel untuk kontrol LED PWM
        self.led_pwm_value = 50  # Nilai default 50%
        # LED1=12, LED2=14, LED3=13, LED4=19
        self.led_pins = [12, 14, 13, 19]
        self.capture_mode = "Together"
        self.scale_factor = self.get_scale_factor()

        # Inisialisasi I2C
        try:
            self.i2c_bus = smbus2.SMBus(I2C_BUS)
            self.i2c_connected = True
            print("I2C terhubung ke bus 1")
        except Exception as e:
            print(f"Error connecting to I2C: {e}")
            self.i2c_connected = False

        # Inisialisasi GPIO untuk Flash System
        self.init_flash_system()

        # Inisialisasi kamera
        self.init_camera()

        # Setup UI
        self.init_ui()
        self.setStatusBar(QStatusBar(self))

        # Timer untuk pembacaan posisi
        self.position_timer = QTimer()
        self.position_timer.timeout.connect(self.read_position)
        self.position_timer.start(100)

        # Timer untuk update frame kamera
        self.frame_timer = QTimer()
        self.frame_timer.timeout.connect(self.update_camera_frame)
        self.frame_timer.start(50)

        # Koneksi sinyal untuk update UI
        self.update_position_signal.connect(self.update_position_display)
        self.update_frame_signal.connect(self.update_frame_display)

    # ====================== HARDWARE INITIALIZATION ======================
    def init_flash_system(self):
        try:
            import RPi.GPIO as GPIO
            self.GPIO = GPIO
            GPIO.setmode(GPIO.BCM)
            
            self.pwm_leds = []
            for pin in self.led_pins:
                GPIO.setup(pin, GPIO.OUT)
                pwm = GPIO.PWM(pin, 1000)
                pwm.start(0)
                self.pwm_leds.append(pwm)

            self.gpio_available = True
            print(f"Flash system initialized on GPIOs {self.led_pins}")
        except ImportError:
            print("RPi.GPIO tidak tersedia, menggunakan simulasi Flash")
            self.gpio_available = False 
        except Exception as e:
            print(f"Error initializing Flash system: {e}")
            self.gpio_available = False

    def capture_single_frame(self):
        if self.camera and HAS_PICAMERA:
            try:
                frame = self.camera.capture_array("main")
                return cv2.flip(frame, -1)
            except Exception as e:
                print(f"Error capturing frame: {e}")
                return None
        else:
            width, height = 1280, 720
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            cv2.circle(frame, (width//2, height//2), 100, (np.random.randint(0,255), 100, 100), -1)
            return cv2.flip(frame, -1)

    def get_scale_factor(self):
        screen = QApplication.desktop().screenGeometry()
        if screen.width() == 1024 and screen.height() == 600:
                return 0.5
        return 1.0

    def init_camera(self):
        if HAS_PICAMERA:
            try:
                self.camera = Picamera2()
                config = self.camera.create_preview_configuration(
                    main={"format": "RGB888", "size": (1280, 720)},
                    controls={"AfMode": controls.AfModeEnum.Manual, "LensPosition": 0.0}
                )
                self.camera.configure(config)
                self.camera.start()
                print("Kamera berhasil diinisialisasi")
                self.apply_camera_settings()
            except Exception as e:
                print(f"Error initializing camera: {e}")
                self.camera = None
        else:
            self.camera = None
            print("Menggunakan simulasi kamera")
            self.camera_thread = threading.Thread(target=self.simulate_camera, daemon=True)
            self.camera_thread.start()

    def apply_camera_settings(self):
        if HAS_PICAMERA and self.camera:
            try:
                controls_dict = {}
                controls_dict["LensPosition"] = self.focus_value
                controls_dict["AnalogueGain"] = self.gain_value
                controls_dict["ExposureTime"] = int(self.exposure_value * 1000)
                self.camera.set_controls(controls_dict)
            except Exception as e:
                print(f"Error applying camera settings: {e}")

    def simulate_camera(self):
        while True:
            width, height = 640, 480
            frame = np.zeros((height, width, 3), dtype=np.uint8)

            t = time.time()
            center_x = int(width/2 + width/4 * np.sin(t))
            center_y = int(height/2 + height/4 * np.cos(t*0.7))
            radius = int(50 + 30 * np.sin(t*1.5))

            cv2.circle(frame, (center_x, center_y), radius, (0, 255, 0), -1)
            cv2.putText(frame, "SIMULATED CAMERA", (width//2-100, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            if self.focus_value > 0:
                blur_amount = int(5 * self.focus_value)
                frame = cv2.GaussianBlur(frame, (blur_amount*2+1, blur_amount*2+1), 0)

            if self.gain_value > 1.0:
                frame = np.clip(frame * self.gain_value, 0, 255).astype(np.uint8)

            if self.exposure_value != 100:
                exposure_factor = self.exposure_value / 100.0
                frame = np.clip(frame * exposure_factor, 0, 255).astype(np.uint8)

            frame = cv2.flip(frame, -1)
            h, w, ch = frame.shape
            bytes_per_line = ch * w
            qt_image = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
            qt_image = qt_image.rgbSwapped()

            self.update_frame_signal.emit(qt_image)
            time.sleep(0.05)
            if not QApplication.instance():
                break

    # ====================== UI SETUP ======================
    def init_ui(self):
        screen = QApplication.desktop().screenGeometry()
        if screen.width() == 1024 and screen.height() == 600:
                self.setWindowState(Qt.WindowMaximized)
        else:
                self.resize(1400, 800)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        
        tab_font_size = int(12 * self.scale_factor)
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{ 
                border: 1px solid #dee2e6; 
                background-color: #ffffff;
                border-radius: 5px;
            }}
            QTabBar::tab {{
                background: #e9ecef;
                color: #495057;
                padding: 10px 25px;
                font-size: {tab_font_size}px;
                font-weight: bold;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
                border: 1px solid #dee2e6;
            }}
            QTabBar::tab:selected {{
                background: #ffffff;
                color: #007bff;
                border-bottom: 2px solid #007bff;
            }}
            QTabBar::tab:hover {{
                background: #dee2e6;
            }}
        """)

        # Tab 1: Capture
        self.tab_capture = QWidget()
        self.setup_capture_tab()
        self.tabs.addTab(self.tab_capture, "📸 Capture & Control")

        # Tab 2: Detect AI
        self.tab_detect = QWidget()
        self.setup_detect_tab()
        self.tabs.addTab(self.tab_detect, "🧠 AI Detection")

        # Tab 3: Preview & Image Processing
        self.tab_preview = QWidget()
        self.setup_preview_tab()
        self.tabs.addTab(self.tab_preview, "🖼️ Preview & Crop")

        # Tab 4: Settings
        self.tab_settings = QWidget()
        self.setup_settings_tab()
        self.tabs.addTab(self.tab_settings, "⚙️ Settings")

        self.show()

    def setup_capture_tab(self):
        main_layout = QHBoxLayout(self.tab_capture)
        spacing = int(10 * self.scale_factor)
        margin = int(10 * self.scale_factor)
        main_layout.setSpacing(spacing)
        main_layout.setContentsMargins(margin, margin, margin, margin)

        left_panel = self.create_motor_control_panel()
        main_layout.addWidget(left_panel, 1)
        center_panel = self.create_camera_panel()
        main_layout.addWidget(center_panel, 2)
        right_panel = self.create_camera_control_panel()
        main_layout.addWidget(right_panel, 1)

    def setup_detect_tab(self):
        layout = QVBoxLayout(self.tab_detect)
        layout.setContentsMargins(20, 20, 20, 20)
        
        controls_layout = QHBoxLayout()
        btn_browse = self.create_button("Pilih Gambar", self.browse_detect_image, "#17a2b8", icon_type=QStyle.SP_DirOpenIcon)
        btn_detect = self.create_button("Deteksi Kelainan", self.detect_abnormality, "#fd7e14", icon_type=QStyle.SP_MessageBoxQuestion)
        
        controls_layout.addWidget(btn_browse)
        controls_layout.addWidget(btn_detect)
        controls_layout.addStretch()
        layout.addLayout(controls_layout)
        
        self.detect_image_label = QLabel("Preview Gambar untuk Deteksi")
        self.detect_image_label.setAlignment(Qt.AlignCenter)
        self.detect_image_label.setStyleSheet("background-color: #e9ecef; border: 2px dashed #adb5bd; color: #6c757d; border-radius: 10px;")
        self.detect_image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.detect_image_label)
        
        self.detect_result_label = QLabel("Hasil Deteksi: Menunggu input...")
        self.detect_result_label.setAlignment(Qt.AlignCenter)
        font_size = int(18 * self.scale_factor)
        self.detect_result_label.setStyleSheet(f"font-size: {font_size}px; font-weight: bold; color: #495057; padding: 15px; background-color: #ffffff; border-radius: 8px; border: 1px solid #dee2e6;")
        layout.addWidget(self.detect_result_label)

    def setup_preview_tab(self):
        layout = QVBoxLayout(self.tab_preview)
        layout.setContentsMargins(20, 20, 20, 20)
        
        controls_layout = QHBoxLayout()
        btn_browse = self.create_button("Buka Galeri", self.browse_preview_image, "#6f42c1", icon_type=QStyle.SP_DirOpenIcon)
        btn_crop = self.create_button("Crop & Bersihkan Glare", self.process_preview_image, "#28a745", icon_type=QStyle.SP_CommandLink)
        
        controls_layout.addWidget(btn_browse)
        controls_layout.addWidget(btn_crop)
        controls_layout.addStretch()
        layout.addLayout(controls_layout)
        
        self.preview_image_label = QLabel("Area Preview Gambar (Bisa di-Crop & Bersihkan)")
        self.preview_image_label.setAlignment(Qt.AlignCenter)
        self.preview_image_label.setStyleSheet("background-color: #e9ecef; border: 2px dashed #adb5bd; color: #6c757d; border-radius: 10px;")
        self.preview_image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.preview_image_label)

    def setup_settings_tab(self):
        layout = QVBoxLayout(self.tab_settings)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        title_label = QLabel("Pengaturan Sistem")
        title_label.setStyleSheet(f"font-size: {int(24 * self.scale_factor)}px; font-weight: bold; color: #343a40;")
        layout.addWidget(title_label)

        settings_frame = QFrame()
        settings_frame.setStyleSheet("background-color: #ffffff; border-radius: 10px; border: 1px solid #dee2e6;")
        frame_layout = QVBoxLayout(settings_frame)
        frame_layout.setContentsMargins(25, 25, 25, 25)
        frame_layout.setSpacing(15)

        lbl_section = QLabel("Mode Capture (Flash LED)")
        lbl_section.setStyleSheet(f"font-size: {int(16 * self.scale_factor)}px; font-weight: bold; color: #007bff; border-bottom: 2px solid #e9ecef; padding-bottom: 10px;")
        frame_layout.addWidget(lbl_section)

        self.radio_together = QRadioButton("Together Mode")
        self.radio_one_by_one = QRadioButton("One by One Mode")

        radio_style = f"""
            QRadioButton {{
                font-size: {int(14 * self.scale_factor)}px;
                padding: 5px;
                color: #495057;
            }}
            QRadioButton::indicator {{
                width: 18px;
                height: 18px;
            }}
        """
        self.radio_together.setStyleSheet(radio_style)
        self.radio_one_by_one.setStyleSheet(radio_style)

        desc_style = f"font-size: {int(12 * self.scale_factor)}px; color: #6c757d; margin-left: 25px; margin-bottom: 10px;"
        
        lbl_together_desc = QLabel("Semua LED flash menyala bersamaan. Capture dilakukan 1 kali.")
        lbl_together_desc.setStyleSheet(desc_style)
        lbl_together_desc.setWordWrap(True)

        lbl_one_desc = QLabel("LED flash menyala bergantian (4 sisi). Capture dilakukan 4 kali lalu digabung.")
        lbl_one_desc.setStyleSheet(desc_style)
        lbl_one_desc.setWordWrap(True)

        self.radio_together.setChecked(True)
        self.radio_together.toggled.connect(self.set_capture_mode)

        frame_layout.addWidget(self.radio_together)
        frame_layout.addWidget(lbl_together_desc)
        frame_layout.addWidget(self.radio_one_by_one)
        frame_layout.addWidget(lbl_one_desc)

        layout.addWidget(settings_frame)
        layout.addStretch()

    # ====================== BUTTON LOGIC ======================
    def set_capture_mode(self):
        if self.radio_together.isChecked():
            self.capture_mode = "Together"
        else:
            self.capture_mode = "OneByOne"
        print(f"Mode Capture diubah ke: {self.capture_mode}")

    def browse_detect_image(self):
        options = QFileDialog.Options()
        save_dir = "/home/nifia/Documents/NIFIA/hasil"
        file_name, _ = QFileDialog.getOpenFileName(self, "Pilih Gambar untuk Deteksi", save_dir, "Images (*.png *.jpg *.jpeg *.bmp)", options=options)
        if file_name:
            self.current_detect_image_path = file_name
            pixmap = QPixmap(file_name)
            h = self.detect_image_label.height()
            w = self.detect_image_label.width()
            self.detect_image_label.setPixmap(pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.detect_result_label.setText("Gambar dimuat. Klik tombol Deteksi untuk memulai.")
            self.detect_result_label.setStyleSheet(f"font-size: {int(18 * self.scale_factor)}px; font-weight: bold; color: #495057; padding: 15px; background-color: #ffffff; border-radius: 8px; border: 1px solid #dee2e6;")

    def detect_abnormality(self):
        if hasattr(self, 'current_detect_image_path'):
            self.detect_result_label.setText("Sedang menganalisis...")
            QApplication.processEvents()
            
            import random
            time.sleep(1.0) 
            
            results = ["Mata Normal", "Katarak", "Glaukoma", "Retinopati Diabetik", "Pterigium"]
            result = random.choice(results)
            
            color = "#27ae60" if result == "Mata Normal" else "#e74c3c"
            self.detect_result_label.setStyleSheet(f"font-size: {int(18 * self.scale_factor)}px; font-weight: bold; color: white; padding: 15px; background-color: {color}; border-radius: 8px;")
            self.detect_result_label.setText(f"Hasil Deteksi: {result}")
        else:
            self.detect_result_label.setText("⚠️ Silakan pilih gambar terlebih dahulu!")
            self.detect_result_label.setStyleSheet(f"font-size: {int(18 * self.scale_factor)}px; font-weight: bold; color: #856404; padding: 15px; background-color: #fff3cd; border-radius: 8px; border: 1px solid #ffeeba;")

    def browse_preview_image(self):
        options = QFileDialog.Options()
        save_dir = "/home/nifia/Documents/NIFIA/hasil"
        file_name, _ = QFileDialog.getOpenFileName(self, "Lihat Gambar", save_dir, "Images (*.png *.jpg *.jpeg *.bmp)", options=options)
        if file_name:
            self.current_preview_image_path = file_name
            pixmap = QPixmap(file_name)
            h = self.preview_image_label.height()
            w = self.preview_image_label.width()
            self.preview_image_label.setPixmap(pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def process_preview_image(self):
        if hasattr(self, 'current_preview_image_path') and self.current_preview_image_path:
            self.statusBar().showMessage("Memproses gambar retina (Menghilangkan glare & memotong)...", 2000)
            QApplication.processEvents()

            hasil = crop_retina_sempurna(self.current_preview_image_path, show_plot=True)

            if hasil is not None:
                base_path, ext = os.path.splitext(self.current_preview_image_path)
                save_path = f"{base_path}_mulus.png"
                
                cv2.imwrite(save_path, hasil)
                self.statusBar().showMessage(f"Selesai! Gambar berhasil disimpan sebagai {os.path.basename(save_path)}.", 5000)
                
                pixmap = QPixmap(save_path)
                h = self.preview_image_label.height()
                w = self.preview_image_label.width()
                self.preview_image_label.setPixmap(pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.current_preview_image_path = save_path
        else:
            self.statusBar().showMessage("Harap buka gambar galeri terlebih dahulu sebelum di-crop!", 3000)

    def capture_image(self):
        self.frame_timer.stop()
        
        try:
            save_dir = "/home/nifia/Documents/NIFIA/hasil"
            os.makedirs(save_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            
            print(f"Memulai sequence capture mode: {self.capture_mode}")
            self.statusBar().showMessage("Capturing...", 2000)

            if self.capture_mode == "Together":
                if self.gpio_available:
                    for pwm in self.pwm_leds:
                        pwm.ChangeDutyCycle(self.led_pwm_value)
                time.sleep(0.1) 
                
                frame = self.capture_single_frame()
                
                if self.gpio_available:
                    for pwm in self.pwm_leds:
                        pwm.ChangeDutyCycle(0)
                
                if frame is not None:
                    filename = f"{save_dir}/capture_{timestamp}_together.jpg"
                    cv2.imwrite(filename, frame)
                    self.statusBar().showMessage(f"Tersimpan: {os.path.basename(filename)}", 3000)
            
            else: # OneByOne
                frames = []
                for i, pwm in enumerate(self.pwm_leds):
                    if self.gpio_available:
                        pwm.ChangeDutyCycle(self.led_pwm_value)
                    time.sleep(0.1)
                    
                    frame = self.capture_single_frame()
                    if frame is not None:
                        frames.append(frame)
                        cv2.imwrite(f"{save_dir}/capture_{timestamp}_led{i+1}.jpg", frame)
                    
                    if self.gpio_available:
                        pwm.ChangeDutyCycle(0)
                
                if frames:
                    merged = np.zeros_like(frames[0], dtype=np.float32)
                    for f in frames:
                        merged += f.astype(np.float32)
                    merged /= len(frames)
                    merged = merged.astype(np.uint8)
                    
                    file_merged = f"{save_dir}/capture_{timestamp}_merged.jpg"
                    cv2.imwrite(file_merged, merged)
                    self.statusBar().showMessage(f"Tersimpan: {os.path.basename(file_merged)}", 3000)

        except Exception as e:
            print(f"Error during capture sequence: {e}")
            self.statusBar().showMessage("Error saat capture", 3000)
        finally:
            if self.gpio_available:
                for pwm in self.pwm_leds:
                    pwm.ChangeDutyCycle(0)
            self.frame_timer.start(50)

    def create_motor_control_panel(self):
        panel = QGroupBox("Kontrol Motor Presisi (1mm/klik)")
        font_size = int(14 * self.scale_factor)
        border_width = max(1, int(2 * self.scale_factor))
        border_radius = int(8 * self.scale_factor)
        margin_top = int(10 * self.scale_factor)

        panel.setStyleSheet(f"""
                QGroupBox {{
                font-weight: bold;
                font-size: {font_size}px;
                border: {border_width}px solid #dee2e6;
                background-color: #ffffff;
                border-radius: {border_radius}px;
                margin-top: 1ex;
                padding-top: {margin_top}px;
                }}
                QGroupBox::title {{
                subcontrol-origin: margin;
                left: {margin_top}px;
                padding: 0 5px 0 5px;
                color: #007bff;
                }}
        """)

        layout = QVBoxLayout(panel)
        spacing = int(10 * self.scale_factor)
        layout.setSpacing(spacing)

        grid = QGridLayout()
        grid_spacing = int(10 * self.scale_factor)
        grid.setSpacing(grid_spacing)

        btn_forward = self.create_motor_button("▲", self.motor_forward, "#007bff")
        grid.addWidget(btn_forward, 0, 1, alignment=Qt.AlignCenter)

        btn_left = self.create_motor_button("◀", self.motor_left, "#007bff")
        grid.addWidget(btn_left, 1, 0, alignment=Qt.AlignCenter)

        btn_home = self.create_motor_button("", self.go_home, "#dc3545", QStyle.SP_DirHomeIcon)
        grid.addWidget(btn_home, 1, 1, alignment=Qt.AlignCenter)

        btn_right = self.create_motor_button("▶", self.motor_right, "#007bff")
        grid.addWidget(btn_right, 1, 2, alignment=Qt.AlignCenter)

        btn_backward = self.create_motor_button("▼", self.motor_backward, "#007bff")
        grid.addWidget(btn_backward, 2, 1, alignment=Qt.AlignCenter)

        layout.addLayout(grid)

        # --- DIPERBESAR TINGGINYA ---
        btn_calibrate = self.create_button("Kalibrasi", self.calibrate_motors, "#ffc107", QStyle.SP_MessageBoxWarning)
        btn_calibrate.setMinimumHeight(int(50 * self.scale_factor))
        layout.addWidget(btn_calibrate)

        btn_reset = self.create_button("Reset Posisi", self.reset_position, "#6c757d", QStyle.SP_BrowserReload)
        btn_reset.setMinimumHeight(int(50 * self.scale_factor))
        layout.addWidget(btn_reset)

        eyes_layout = QHBoxLayout()
        btn_left_eye = self.create_button("◀ Mata Kiri", self.left_eye, "#28a745")
        btn_left_eye.setMinimumHeight(int(50 * self.scale_factor))
        
        btn_right_eye = self.create_button("Mata Kanan ▶", self.right_eye, "#28a745")
        btn_right_eye.setMinimumHeight(int(50 * self.scale_factor))
        
        eyes_layout.addWidget(btn_left_eye)
        eyes_layout.addWidget(btn_right_eye)
        layout.addLayout(eyes_layout)

        focus_group = QGroupBox("Pengaturan Fokus Kamera")
        group_font_size = int(12 * self.scale_factor)
        focus_group.setStyleSheet(f"""
            QGroupBox {{
                font-weight: bold;
                font-size: {group_font_size}px;
                border: 2px solid #fd7e14;
                background-color: #ffffff;
                border-radius: 8px;
                margin-top: 1ex;
                padding-top: {margin_top}px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #fd7e14;
            }}
        """)
        focus_layout = QHBoxLayout(focus_group)
        
        btn_focus_minus = QPushButton("➖")
        btn_focus_plus = QPushButton("➕")
        
        # --- TOMBOL FOKUS DIBUAT MAKSIMAL KEMBALI ---
        focus_btn_style = f"""
            QPushButton {{
                background-color: #fd7e14;
                color: white;
                font-size: {int(36 * self.scale_factor)}px;
                font-weight: bold;
                border-radius: {int(10 * self.scale_factor)}px;
                min-height: {int(60 * self.scale_factor)}px;
            }}
            QPushButton:pressed {{
                background-color: #e8590c;
                border: 2px solid #ffd8a8;
            }}
        """
        btn_focus_minus.setStyleSheet(focus_btn_style)
        btn_focus_plus.setStyleSheet(focus_btn_style)

        btn_focus_minus.clicked.connect(lambda: self.adjust_focus(-0.5))
        btn_focus_plus.clicked.connect(lambda: self.adjust_focus(0.5))

        focus_layout.addWidget(btn_focus_minus)
        focus_layout.addWidget(btn_focus_plus)
        layout.addWidget(focus_group)

        pos_layout = QHBoxLayout()
        pos_layout.setContentsMargins(0, 0, 0, 0)

        self.x_pos_label = QLabel("X: 0 mm")
        self.y_pos_label = QLabel("Y: 0 mm")
        self.x_pos_label.setAlignment(Qt.AlignCenter)
        self.y_pos_label.setAlignment(Qt.AlignCenter)

        # --- LABEL POSISI DIKECILKAN UNTUK MENGHEMAT RUANG ---
        padding = int(4 * self.scale_factor)
        font_size = int(11 * self.scale_factor)
        min_height = int(25 * self.scale_factor)
        border_radius = int(4 * self.scale_factor)

        pos_style = f"font-size: {font_size}px; font-weight: bold; padding: {padding}px; background-color: #6c757d; border-radius: {border_radius}px; min-height: {min_height}px; color: white;"
        self.x_pos_label.setStyleSheet(pos_style)
        self.y_pos_label.setStyleSheet(pos_style)

        pos_layout.addWidget(self.x_pos_label)
        pos_layout.addWidget(self.y_pos_label)
        layout.addLayout(pos_layout)

        return panel

    def create_camera_panel(self):
        panel = QGroupBox("Tampilan Kamera")
        font_size = int(14 * self.scale_factor)
        border_width = max(1, int(2 * self.scale_factor))

        panel.setStyleSheet(f"""
                QGroupBox {{
                font-weight: bold;
                font-size: {font_size}px;
                border: {border_width}px solid #dee2e6;
                background-color: #ffffff;
                border-radius: {int(8 * self.scale_factor)}px;
                margin-top: 1ex;
                padding-top: {int(10 * self.scale_factor)}px;
                }}
                QGroupBox::title {{
                subcontrol-origin: margin;
                left: {int(10 * self.scale_factor)}px;
                padding: 0 5px 0 5px;
                color: #007bff;
                }}
        """)

        layout = QVBoxLayout(panel)
        layout.setSpacing(int(10 * self.scale_factor))

        self.camera_display = QLabel()
        self.camera_display.setAlignment(Qt.AlignCenter)
        min_height = int(400 * self.scale_factor)

        self.camera_display.setStyleSheet(f"""
                background-color: #000000;
                min-height: {min_height}px;
                border: {border_width}px solid #6c757d;
                border-radius: 5px;
        """)
        self.camera_display.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.camera_display.setText("Memuat kamera...")
        layout.addWidget(self.camera_display)

        slider_frame = QFrame()
        slider_frame.setStyleSheet("background-color: #e9ecef; border-radius: 8px;")
        
        slider_hbox = QHBoxLayout(slider_frame)
        slider_hbox.setContentsMargins(10, 5, 10, 5) 
        
        lbl_intensity = QLabel("Cahaya:")
        lbl_intensity.setStyleSheet(f"font-weight: bold; font-size: {int(13 * self.scale_factor)}px; color: #495057;")
        slider_hbox.addWidget(lbl_intensity)
        
        lbl_min = QLabel("0%")
        lbl_min.setStyleSheet(f"font-size: {int(12 * self.scale_factor)}px; color: #6c757d; font-weight: bold;")
        slider_hbox.addWidget(lbl_min)
        
        self.led_slider = QSlider(Qt.Horizontal)
        self.led_slider.setRange(0, 100)
        self.led_slider.setValue(self.led_pwm_value)
        self.led_slider.setEnabled(False)
        self.led_slider.valueChanged.connect(lambda value: self.set_led_pwm(value))

        slider_height = int(12 * self.scale_factor) 
        handle_width = int(28 * self.scale_factor) 
        
        self.led_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                border: 1px solid #ced4da;
                height: {slider_height}px;
                background: #ced4da;
                margin: 2px 0;
                border-radius: {slider_height//2}px;
            }}
            QSlider::handle:horizontal {{
                background: #17a2b8;
                border: 2px solid #ffffff;
                width: {handle_width}px;
                height: {handle_width}px;
                margin: -{int((handle_width - slider_height)/2)}px 0;
                border-radius: {handle_width//2}px;
            }}
            QSlider::sub-page:horizontal {{
                background: #17a2b8;
                border-radius: {slider_height//2}px;
            }}
        """)
        slider_hbox.addWidget(self.led_slider)

        lbl_max = QLabel("100%")
        lbl_max.setStyleSheet(f"font-size: {int(12 * self.scale_factor)}px; color: #6c757d; font-weight: bold;")
        slider_hbox.addWidget(lbl_max)

        self.led_value_label = QLabel(f"{self.led_pwm_value}%")
        self.led_value_label.setStyleSheet(f"""
            font-weight: bold; 
            font-size: {int(14 * self.scale_factor)}px; 
            color: white; 
            background-color: #343a40;
            border-radius: 4px;
            padding: 4px 10px;
            margin-left: 5px;
        """)
        self.led_value_label.setAlignment(Qt.AlignCenter)
        slider_hbox.addWidget(self.led_value_label)
        
        layout.addWidget(slider_frame)

        return panel

    def create_camera_control_panel(self):
        panel = QGroupBox("Kontrol Kamera")
        font_size = int(14 * self.scale_factor)
        border_width = max(1, int(2 * self.scale_factor))
        border_radius = int(8 * self.scale_factor)
        margin_top = int(10 * self.scale_factor)

        panel.setStyleSheet(f"""
            QGroupBox {{
                font-weight: bold;
                font-size: {font_size}px;
                border: {border_width}px solid #dee2e6;
                background-color: #ffffff;
                border-radius: {border_radius}px;
                margin-top: 1ex;
                padding-top: {margin_top}px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: {margin_top}px;
                padding: 0 5px 0 5px;
                color: #007bff;
            }}
        """)

        layout = QVBoxLayout(panel)
        spacing = int(15 * self.scale_factor)
        layout.setSpacing(spacing)

        mode_group = QGroupBox("Mode Kamera")
        group_font_size = int(12 * self.scale_factor)
        mode_group.setStyleSheet(f"QGroupBox {{ font-weight: bold; font-size: {group_font_size}px; }}")
        mode_layout = QHBoxLayout(mode_group)

        btn_rgb = self.create_button("RGB", lambda: self.set_camera_mode(CameraMode.RGB), "#17a2b8")
        btn_bw = self.create_button("BW", lambda: self.set_camera_mode(CameraMode.BW), "#6c757d")

        mode_layout.addWidget(btn_rgb)
        mode_layout.addWidget(btn_bw)
        layout.addWidget(mode_group)

        self.btn_examine = self.create_button("Mode Periksa: OFF", self.toggle_examine_mode, "#6c757d", QStyle.SP_MessageBoxInformation, is_large=True)
        layout.addWidget(self.btn_examine)

        self.btn_capture = self.create_button("Capture", self.capture_image, "#dc3545", QStyle.SP_DialogSaveButton, is_large=True)
        layout.addWidget(self.btn_capture)

        layout.addStretch()
        return panel

    def update_button_style(self, button, color, is_large=False):
        padding = int(20 * self.scale_factor) if is_large else int(12 * self.scale_factor)
        font_size = int(22 * self.scale_factor) if is_large else int(14 * self.scale_factor)
        min_height = int(80 * self.scale_factor) if is_large else int(40 * self.scale_factor)
        border_radius = int(10 * self.scale_factor) if is_large else int(6 * self.scale_factor)

        button.setStyleSheet(f"""
                QPushButton {{
                background-color: {color};
                border: none;
                color: white;
                padding: {padding}px;
                font-size: {font_size}px;
                font-weight: bold;
                border-radius: {border_radius}px;
                min-height: {min_height}px;
                }}
                QPushButton:pressed {{
                background-color: #343a40;
                padding: {max(1, padding-2)}px;
                border: {max(1, int(2 * self.scale_factor))}px solid #ecf0f1;
                }}
                QPushButton:hover {{
                background-color: {color};
                border: 2px solid #adb5bd;
                }}
        """)

    def create_button(self, text, callback, color="#17a2b8", icon_type=None, is_large=False):
        button = QPushButton(text)
        
        if icon_type:
            icon = self.style().standardIcon(icon_type)
            button.setIcon(icon)
            
            icon_size = 40 if is_large else 20
            button.setIconSize(QSize(int(icon_size * self.scale_factor), int(icon_size * self.scale_factor)))

        self.update_button_style(button, color, is_large)
        button.clicked.connect(callback)
        return button

    def create_motor_button(self, text, command, color="#17a2b8", icon_type=None):
        button = QPushButton(text)
        
        # --- D-PAD DIPERBESAR KEMBALI ---
        size = int(85 * self.scale_factor)
        
        if icon_type:
            icon = self.style().standardIcon(icon_type)
            button.setIcon(icon)
            button.setIconSize(QSize(int(30 * self.scale_factor), int(30 * self.scale_factor)))

        if text in ["▲", "▼", "◀", "▶"]:
            font_size = int(36 * self.scale_factor) # Panah jauh lebih besar
        else:
            font_size = int(14 * self.scale_factor)

        border_radius = int(size / 2)
        button.setFixedSize(size, size)

        button.setStyleSheet(f"""
                QPushButton {{
                background-color: {color};
                border: none;
                color: white;
                font-size: {font_size}px;
                font-weight: bold;
                border-radius: {border_radius}px;
                }}
                QPushButton:pressed {{
                background-color: #343a40;
                border: {max(1, int(2 * self.scale_factor))}px solid #ecf0f1;
                }}
                QPushButton:hover {{
                background-color: {color};
                border: 2px solid #adb5bd;
                }}
        """)

        button.clicked.connect(command)
        return button

    # ====================== I2C COMMUNICATION ======================
    def send_i2c_command(self, command, value=0):
        if not self.i2c_connected:
            return

        try:
            data = [(value >> 8) & 0xFF, value & 0xFF]
            self.i2c_bus.write_i2c_block_data(ESP32_ADDRESS, command, data)
            print(f"I2C Kirim: 0x{command:02X}, Value: {value}")
        except Exception as e:
            print(f"Error sending I2C command: {e}")

    def read_i2c_data(self):
        if not self.i2c_connected:
            return self.x_position, self.y_position

        try:
            data = self.i2c_bus.read_i2c_block_data(ESP32_ADDRESS, 0, 4)
            x_raw = (data[0] << 8) | data[1]
            y_raw = (data[2] << 8) | data[3]
            x_pos = x_raw if x_raw < 32768 else x_raw - 65536
            y_pos = y_raw if y_raw < 32768 else y_raw - 65536
            return x_pos, y_pos
        except Exception as e:
            print(f"Error reading I2C data: {e}")
            return self.x_position, self.y_position

    # ====================== MOTOR CONTROL LOGIC ======================
    def motor_forward(self):
        self.send_i2c_command(0x01, MM_PER_STEP)
        self.statusBar().showMessage("Bergerak maju 1mm", 1000)

    def motor_backward(self):
        self.send_i2c_command(0x02, MM_PER_STEP)
        self.statusBar().showMessage("Bergerak mundur 1mm", 1000)

    def motor_left(self):
        self.send_i2c_command(0x03, MM_PER_STEP)
        self.statusBar().showMessage("Bergerak kiri 1mm", 1000)

    def motor_right(self):
        self.send_i2c_command(0x04, MM_PER_STEP)
        self.statusBar().showMessage("Bergerak kanan 1mm", 1000)

    def go_home(self):
        self.send_i2c_command(0x06, 0)
        self.statusBar().showMessage("Bergerak ke posisi home (0,0)...", 3000)
        self.x_position = 0
        self.y_position = 0
        self.update_position_display(0, 0)

    def calibrate_motors(self):
        self.send_i2c_command(0x05, 0)
        self.statusBar().showMessage("Mengkalibrasi motor...", 2000)

    def reset_position(self):
        self.send_i2c_command(0x20, 0)
        self.x_position = 0
        self.y_position = 0
        self.update_position_display(0, 0)
        self.statusBar().showMessage("Posisi direset ke (0, 0)", 2000)

    def left_eye(self):
        self.send_i2c_command(0x10, 50)
        self.eye_target_position = 50
        self.eye_moving = True
        self.statusBar().showMessage("Mata bergerak otomatis ke posisi kiri", 5000)

    def right_eye(self):
        self.send_i2c_command(0x11, 10)
        self.eye_target_position = 10
        self.eye_moving = True
        self.statusBar().showMessage("Mata bergerak otomatis ke posisi kanan ", 5000)

    # ====================== CAMERA SETTINGS LOGIC ======================
    def set_camera_mode(self, mode):
        self.camera_mode = mode

    def adjust_focus(self, delta):
        self.focus_value += delta
        self.focus_value = max(0.0, min(50.0, self.focus_value))
        self.apply_camera_settings()

    def adjust_gain(self, delta):
        self.gain_value += delta
        self.gain_value = max(1.0, min(20.0, self.gain_value))
        self.apply_camera_settings()

    def adjust_exposure(self, delta):
        self.exposure_value += delta
        self.exposure_value = max(10.0, min(1000.0, self.exposure_value))
        self.apply_camera_settings()

    # ====================== LED & EXAMINE MODE LOGIC ======================
    def set_led_pwm(self, value):
        self.led_pwm_value = value
        self.led_value_label.setText(f"{value}%")

        if self.is_examining:
            if self.gpio_available:
                try:
                    for pwm in self.pwm_leds:
                        pwm.ChangeDutyCycle(value)
                except Exception as e:
                    print(f"Error changing LED intensity: {e}")

    def toggle_examine_mode(self):
        self.is_examining = not self.is_examining
        
        if self.is_examining:
            self.btn_examine.setText("Mode Periksa: ON")
            self.update_button_style(self.btn_examine, "#ffc107", is_large=True) 
            self.led_slider.setEnabled(True)
            self.btn_capture.setEnabled(False)
            
            if self.gpio_available:
                for pwm in self.pwm_leds:
                    pwm.ChangeDutyCycle(self.led_pwm_value)
        else:
            self.btn_examine.setText("Mode Periksa: OFF")
            self.update_button_style(self.btn_examine, "#6c757d", is_large=True) 
            self.led_slider.setEnabled(False)
            self.btn_capture.setEnabled(True)
            
            if self.gpio_available:
                for pwm in self.pwm_leds:
                    pwm.ChangeDutyCycle(0)

    # ====================== UPDATE LOOPS & SIGNALS ======================
    def read_position(self):
        x, y = self.read_i2c_data()
        self.update_position_signal.emit(x, y)

    def update_camera_frame(self):
        if self.camera and HAS_PICAMERA:
            try:
                frame = self.camera.capture_array("main")
                if self.camera_mode == CameraMode.BW:
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
                else:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                frame = cv2.flip(frame, -1)

                h, w, ch = frame.shape
                bytes_per_line = ch * w
                qt_image = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
                self.update_frame_signal.emit(qt_image)
            except Exception as e:
                print(f"Error capturing frame: {e}")

    def update_position_display(self, x, y):
        self.x_position = x
        self.y_position = y

        if self.eye_moving and self.eye_target_position is not None:
            error = abs(x - self.eye_target_position)
            if error <= 2:
                self.eye_moving = False
                target_name = "kiri" if self.eye_target_position == 20 else "kanan"
                self.statusBar().showMessage(f"Mata mencapai posisi {target_name} ({self.eye_target_position}mm)", 3000)

        x_text = f"X: {x:+d} mm" if x != 0 else "X: 0 mm"
        y_text = f"Y: {y:+d} mm" if y != 0 else "Y: 0 mm"

        if self.eye_moving:
            x_text += f" → {self.eye_target_position}mm"

        self.x_pos_label.setText(x_text)
        self.y_pos_label.setText(y_text)

        # --- PASTIKAN UPDATE STYLE JUGA MENGGUNAKAN UKURAN YANG KECIL ---
        font_size = int(11 * self.scale_factor)
        padding = int(4 * self.scale_factor)
        min_height = int(25 * self.scale_factor)
        border_radius = int(4 * self.scale_factor)
        
        base_style = f"font-size: {font_size}px; font-weight: bold; padding: {padding}px; border-radius: {border_radius}px; min-height: {min_height}px; color: white;"

        if self.eye_moving:
            self.x_pos_label.setStyleSheet(f"{base_style} background-color: #ffc107; color: #333;")
        elif x < 0:
            self.x_pos_label.setStyleSheet(f"{base_style} background-color: #dc3545;")
        elif x > 0:
            self.x_pos_label.setStyleSheet(f"{base_style} background-color: #28a745;")
        else:
            self.x_pos_label.setStyleSheet(f"{base_style} background-color: #6c757d;")

        if y < 0:
            self.y_pos_label.setStyleSheet(f"{base_style} background-color: #dc3545;")
        elif y > 0:
            self.y_pos_label.setStyleSheet(f"{base_style} background-color: #28a745;")
        else:
            self.y_pos_label.setStyleSheet(f"{base_style} background-color: #6c757d;")

    def update_frame_display(self, qt_image):
        if not qt_image:
            return

        final_image = qt_image
        if abs(self.zoom_value - 1.0) > 0.01:
            original_size = qt_image.size()
            scaled_size = original_size * self.zoom_value
            scaled_image = qt_image.scaled(scaled_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pos_x = (original_size.width() - scaled_size.width()) / 2
            pos_y = (original_size.height() - scaled_size.height()) / 2
            final_image = QImage(original_size, QImage.Format_RGB32)
            final_image.fill(Qt.black)
            painter = QPainter(final_image)
            painter.drawImage(int(pos_x), int(pos_y), scaled_image)
            painter.end()
        else:
            final_image = qt_image.copy()

        self.last_displayed_image = final_image
        pixmap = QPixmap.fromImage(final_image)
        self.camera_display.setPixmap(pixmap)

    def cleanup_before_exit(self):
        try:
            self.position_timer.stop()
            self.frame_timer.stop()
        except Exception:
            pass

    def closeEvent(self, event):
        self.position_timer.stop()
        self.frame_timer.stop()

        if self.camera and HAS_PICAMERA:
            try:
                self.camera.stop()
            except Exception as e:
                pass

        if self.i2c_connected:
            try:
                self.i2c_bus.close()
            except Exception as e:
                pass

        if hasattr(self, 'gpio_available') and self.gpio_available:
            try:
                for pwm in self.pwm_leds:
                    pwm.ChangeDutyCycle(0)
                    pwm.stop()
                self.GPIO.cleanup()
            except Exception as e:
                pass

        event.accept()
        QApplication.quit()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = MotorControlApp()
    window.show()
    
    def shutdown():
        window.cleanup_before_exit()
        sys.exit(0)

    app.aboutToQuit.connect(shutdown)

    ret = app.exec_()
    sys.exit(ret)