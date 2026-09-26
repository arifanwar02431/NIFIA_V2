# NIFIA V2 (Non-Invasive Retinal Microvascular Analyzer)

**NIFIA V2** adalah sistem desktop berbasis GUI untuk akuisisi, kontrol pergerakan presisi, dan analisis citra mikrovaskular retina mata secara non-invasif. Sistem ini dirancang untuk dijalankan pada **Raspberry Pi 5** yang terintegrasi dengan sensor kamera mikroskopis, iluminasi flash LED multi-channel, dan aktuator motor stepper via mikrokontroler ESP32.

---

## 🌟 Fitur Utama

1. **Precision Motor & Eye Tracking Control:**
   * Navigasi motor stepper 2-axis (X/Y) dengan resolusi presisi 1 mm per langkah.
   * Komunikasi serial inter-IC (**I2C**) menuju slave controller (ESP32 pada alamat `0x08`).
   * Tombol pintas tracking otomatis untuk posisi mata kiri dan mata kanan serta fungsi kalibrasi/home.

2. **Camera & Iluminasi Flash System:**
   * Integrasi **Picamera2** & **libcamera** untuk live preview dengan kontrol fokus lensa manual, analog gain, dan exposure time.
   * Kontrol 4-Channel LED PWM (GPIO 12, 14, 13, 19) dengan dua mode penangkapan:
     * **Together Mode:** Semua LED aktif bersamaan untuk single-shot capture.
     * **One by One Mode:** LED aktif bergantian 4 sisi untuk menghasilkan komposit rata-rata (multi-frame blending).

3. **Digital Image Processing & Glare Removal:**
   * Algoritma penghilangan pantulan cahaya (*glare inpainting*) berbasis *Telea Algorithm*.
   * Segmentasi warna retina adaptif HSV + *Otsu Thresholding*.
   * Pemotongan otomatis kontur lingkaran bola mata sempurna (*circular mask + alpha transparency channel*).

4. **Screen Scaling Adaptation:**
   * Deteksi resolusi layar otomatis untuk kompatibilitas tampilan layar sentuh 1024x600 maupun monitor standar.

---

## 🛠️ Hardware Requirements & Pinout

* **Komputasi Utama:** Raspberry Pi 5 (Raspberry Pi OS Bookworm 64-bit)
* **Slave Motion Controller:** ESP32 (I2C Bus 1, Address `0x08`)
* **Kamera:** Raspberry Pi Camera Module / High Quality Camera (Mendukung libcamera / Picamera2)
* **Flash System (PWM LED):**
  * LED 1: `GPIO 12`
  * LED 2: `GPIO 14`
  * LED 3: `GPIO 13`
  * LED 4: `GPIO 19`
* **Jalur I2C:**
  * SDA: `GPIO 2`
  * SCL: `GPIO 3`

---

## 📦 Panduan Instalasi (Langkah demi Langkah)

Jika Anda menggunakan Raspberry Pi 5 baru, ikuti panduan berikut agar aplikasi beserta library Python dan sistem operasi terkonfigurasi dengan benar tanpa merusak proteksi sistem (PEP 668).

### 1. Aktifkan I2C di Raspberry Pi
Aplikasi ini membutuhkan jalur I2C untuk berkomunikasi dengan ESP32. Buka terminal dan ketik:
```bash
sudo raspi-config
