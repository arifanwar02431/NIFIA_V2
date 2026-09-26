NIFIA V2 (Non-Invasive Retinal Microvascular Analyzer)

NIFIA V2 adalah sistem desktop berbasis GUI untuk akuisisi, kontrol pergerakan presisi, dan analisis citra mikrovaskular retina mata secara non-invasif. Sistem ini dirancang untuk dijalankan pada Raspberry Pi 5 yang terintegrasi dengan sensor kamera mikroskopis, iluminasi flash LED multi-channel, dan aktuator motor stepper via mikrokontroler ESP32.

🌟 Fitur Utama

Precision Motor & Eye Tracking Control: Navigasi motor stepper presisi tinggi via I2C (ESP32).

Camera & Iluminasi Flash System: Kontrol kamera (Picamera2) dan 4-Channel LED PWM (Together / One-by-One mode).

Digital Image Processing: Penghilangan pantulan cahaya (glare) dan pemotongan (crop) otomatis retina menjadi bulat sempurna.

Adaptive UI: Skala GUI otomatis menyesuaikan monitor resolusi standar maupun layar sentuh 1024x600.

🛠️ Hardware Requirements & Pinout

Komputasi Utama: Raspberry Pi 5 (Raspberry Pi OS Bookworm 64-bit)

Kamera: Raspberry Pi Camera Module (Mendukung libcamera / Picamera2)

Slave Controller: ESP32 (I2C Bus 1, Address 0x08)

SDA: GPIO 2 | SCL: GPIO 3

Flash System (PWM LED):

LED 1: GPIO 12 | LED 2: GPIO 14 | LED 3: GPIO 13 | LED 4: GPIO 19

📦 Panduan Instalasi Lengkap (Langkah demi Langkah)

Ikuti langkah-langkah di bawah ini secara berurutan pada Raspberry Pi 5 Anda yang baru diinstal ulang (OS Debian Bookworm).

⚠️ PERSIAPAN PENTING (Wajib Dibaca)

Sistem ini menggunakan path (alamat folder) yang statis di dalam kodenya.
Pastikan username Raspberry Pi Anda adalah nifia.
Jika username Anda bukan nifia, Anda harus membuat folder yang sesuai terlebih dahulu.

Langkah 1: Update Sistem & Install Python 3

Buka terminal Raspberry Pi Anda (ikon hitam di pojok kiri atas) dan jalankan perintah berikut untuk memastikan OS dan Python sudah yang terbaru:

# Update seluruh sistem OS
sudo apt update && sudo apt full-upgrade -y

# Install Python 3, pip, Virtual Environment, dan Git
sudo apt install python3 python3-pip python3-venv git -y


Langkah 2: Aktifkan Komunikasi I2C (Untuk Motor ESP32)

Aplikasi butuh I2C aktif untuk mengirim perintah ke motor:

sudo raspi-config


Gunakan panah bawah, pilih menu 3 Interface Options -> tekan Enter.

Pilih I4 I2C -> tekan Enter.

Pilih Yes untuk mengaktifkannya.

Pilih Finish dan reboot (restart) Raspberry Pi Anda jika diminta.

Langkah 3: Download Source Code (Clone dari GitHub)

Setelah restart, buka terminal lagi. Kita harus meletakkan file persis di folder /home/nifia/Documents/NIFIA agar aplikasi bisa berjalan.

# Buat folder Documents jika belum ada, lalu masuk ke dalamnya
mkdir -p /home/nifia/Documents
cd /home/nifia/Documents

# Download aplikasi dari GitHub dengan nama folder "NIFIA"
git clone https://github.com/arifanwar02431/NIFIA_V2.git NIFIA

# Masuk ke folder aplikasi
cd NIFIA


Langkah 4: Install Library Otomatis via Script

Karena Raspberry Pi 5 (OS Bookworm) melarang instalasi library sembarangan (aturan PEP 668), kita gunakan script yang sudah disiapkan untuk menginstal lewat Virtual Environment.

# Berikan izin agar script instalasi bisa dijalankan
chmod +x install.sh

# Jalankan script (proses ini butuh koneksi internet dan waktu beberapa menit)
./install.sh


Script ini akan otomatis menginstal paket sistem dari apt-packages.txt dan paket Python dari requirements.txt ke dalam folder env/.

Langkah 5: Konfigurasi Desktop Shortcut (Launcher Aplikasi)

Agar Anda tidak perlu membuka terminal setiap kali ingin memakai aplikasi, mari buat tombol aplikasinya di Desktop.

Jika Anda menggunakan Virtual Environment (dari Langkah 4), Anda perlu sedikit mengedit file shortcut agar menggunakan Python dari environment tersebut:

# 1. Edit file desktop menggunakan nano
nano NIFIA3.desktop


Cari baris yang bertuliskan: Exec=python3 /home/nifia/Documents/NIFIA/NIFIA_HPL.py
Ubah menjadi:
Exec=/home/nifia/Documents/NIFIA/env/bin/python3 /home/nifia/Documents/NIFIA/NIFIA_HPL.py

Tekan Ctrl+X, lalu Y, lalu Enter untuk menyimpan.

# 2. Copy file tersebut ke Desktop
cp NIFIA3.desktop ~/Desktop/

# 3. Berikan izin eksekusi agar bisa diklik ganda
chmod +x ~/Desktop/NIFIA3.desktop


Sekarang, jika ada prompt peringatan "Untrusted application launcher" saat Anda mengkliknya di Desktop, pilih Trust and Launch.

🚀 Cara Menjalankan Aplikasi Manual (Tanpa Desktop Shortcut)

Jika suatu saat shortcut tidak berfungsi, Anda selalu bisa menjalankan NIFIA V2 melalui terminal:

# 1. Masuk ke folder aplikasi
cd /home/nifia/Documents/NIFIA

# 2. Aktifkan Virtual Environment
source env/bin/activate

# 3. Jalankan aplikasi GUI
python3 NIFIA_HPL.py


📁 Direktori Penyimpanan Gambar

Pastikan Anda tidak menghapus folder ini, karena sistem akan menyimpan hasil kamera dan hasil crop retina secara otomatis di sini:

/home/nifia/Documents/NIFIA/hasil/


Author: Arif Anwar Rosyidin | GitHub: @arifanwar02431
