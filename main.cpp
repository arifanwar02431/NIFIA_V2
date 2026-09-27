#include <Arduino.h>

#include <Wire.h>

// ====================== KONFIGURASI PIN ======================
// Motor X
#define MOTOR_X_L_PWM 38
#define MOTOR_X_R_PWM 37
// Motor Y
#define MOTOR_Y_L_PWM 40
#define MOTOR_Y_R_PWM 39

// Encoder
#define ENCODER1_A 11
#define ENCODER1_B 10
#define ENCODER2_A 13
#define ENCODER2_B 12

// Enable Pin (Driver Motor)
#define EN 15

// Pin LED
#define LED1_PIN 16
#define LED2_PIN 17

// Alamat I2C
#define I2C_ADDRESS 0x08

// ====================== KONFIGURASI KALIBRASI ======================
#define CALIB_PWM 80          // Kecepatan motor saat kalibrasi (biar pelan & aman)
#define STALL_TIME_MS 500     // Waktu tunggu (ms) sebelum dianggap mentok
#define CALIB_TOLERANCE 2     // Toleransi getaran encoder (tick)

// Encoder parameters
#define CPR 92.5              // Counts Per Revolution
#define PITCH 1.0             // mm per revolution
#define MM_PER_COUNT (PITCH / CPR)

// ====================== VARIABEL GLOBAL ======================
// Posisi Encoder Raw
volatile long encoder1Count = 0;
volatile long encoder2Count = 0;

// Posisi dalam mm
volatile int16_t position1_mm = 0;  // X
volatile int16_t position2_mm = 0;  // Y

// Status PWM Motor saat ini
volatile int motorXPWM = 0;
volatile int motorYPWM = 0;

// Variabel Komunikasi I2C
volatile uint8_t received_cmd = 0;
volatile int received_value = 0;
volatile bool new_command = false;
uint8_t positionData[4]; // Buffer kirim data

// Variabel Kontrol Gerak (Normal Movement)
volatile bool isMovingX = false;
volatile bool isMovingY = false;
volatile int targetDistanceX = 0;
volatile int targetDistanceY = 0;
volatile long initialEncoderCountX = 0;
volatile long initialEncoderCountY = 0;

// Variabel Kontrol Kalibrasi (Homing)
volatile bool isCalibrating = false;
bool calibXDone = false;
bool calibYDone = false;
long lastCalibEncX = 0;
long lastCalibEncY = 0;
unsigned long lastMoveTimeX = 0;
unsigned long lastMoveTimeY = 0;

// FreeRTOS Task handles
TaskHandle_t TaskI2C;
TaskHandle_t TaskMotor;

// Forward Declaration
void executeCommand();

// ====================== INTERRUPT SERVICE ROUTINES (ISR) ======================
void IRAM_ATTR encoder1ISR() {
  // Dibalik agar saat motor X bergerak ke Kanan (PWM Positif), nilai encoder bertambah (+)
  // dan saat bergerak ke Kiri (PWM Negatif), nilai encoder berkurang (-)
  if (digitalRead(ENCODER1_B)) encoder1Count--;
  else encoder1Count++;
}

void IRAM_ATTR encoder2ISR() {
  // Dibalik agar konsisten dengan arah motor Y.
  // Saat motor bergerak ke arah Positif (menjauhi titik nol), nilai encoder harus bertambah.
  if (digitalRead(ENCODER2_B)) encoder2Count--;
  else encoder2Count++;
}

// ====================== FUNGSI PENGGERAK MOTOR ======================
void setMotorX(int pwm) {
  motorXPWM = constrain(pwm, -255, 255);

  if (motorXPWM > 0) {
    analogWrite(MOTOR_X_L_PWM, motorXPWM);
    analogWrite(MOTOR_X_R_PWM, 0);
  } else if (motorXPWM < 0) {
    analogWrite(MOTOR_X_L_PWM, 0);
    analogWrite(MOTOR_X_R_PWM, -motorXPWM);
  } else {
    analogWrite(MOTOR_X_L_PWM, 0);
    analogWrite(MOTOR_X_R_PWM, 0);
  }
}

void setMotorY(int pwm) {
  motorYPWM = constrain(pwm, -255, 255);

  if (motorYPWM > 0) {
    analogWrite(MOTOR_Y_L_PWM, motorYPWM);
    analogWrite(MOTOR_Y_R_PWM, 0);
  } else if (motorYPWM < 0) {
    analogWrite(MOTOR_Y_L_PWM, 0);
    analogWrite(MOTOR_Y_R_PWM, -motorYPWM);
  } else {
    analogWrite(MOTOR_Y_L_PWM, 0);
    analogWrite(MOTOR_Y_R_PWM, 0);
  }
}

// ====================== FUNGSI GERAK NORMAL ======================
void startMotorXMovement(int distance_mm, int pwm) {
  if (isCalibrating) return; 
  if (distance_mm == 0) return; // SAFETY: Abaikan jika jarak 0
  
  isMovingX = true;
  targetDistanceX = distance_mm;
  initialEncoderCountX = encoder1Count;
  
  int direction = (distance_mm > 0) ? 1 : -1;
  setMotorX(direction * pwm);
  
  Serial.printf("START X: %d mm (PWM %d)\n", distance_mm, direction * pwm);
}

void startMotorYMovement(int distance_mm, int pwm) {
  if (isCalibrating) return; 
  if (distance_mm == 0) return; // SAFETY: Abaikan jika jarak 0

  isMovingY = true;
  targetDistanceY = distance_mm;
  initialEncoderCountY = encoder2Count;
  
  int direction = (distance_mm > 0) ? 1 : -1;
  setMotorY(direction * pwm);
  
  Serial.printf("START Y: %d mm (PWM %d)\n", distance_mm, direction * pwm);
}

// ====================== FUNGSI KALIBRASI (INDEPENDEN) ======================
void startCalibration() {
  Serial.println("=== KALIBRASI DIMULAI (INDEPENDEN) ===");
  
  // 1. Reset state gerak normal
  isMovingX = false;
  isMovingY = false;
  
  // 2. Set flag kalibrasi
  isCalibrating = true;
  calibXDone = false;
  calibYDone = false;
  
  // 3. Catat posisi awal
  lastCalibEncX = encoder1Count;
  lastCalibEncY = encoder2Count;

  // 4. Reset timer
  lastMoveTimeX = millis();
  lastMoveTimeY = millis();
  
  // 5. Jalankan KEDUA motor perlahan (PWM 80)
  // Motor X bergerak ke arah negatif (kiri) untuk kalibrasi.
  // Motor Y diasumsikan bergerak ke arah positif untuk kalibrasi.
  setMotorX(-CALIB_PWM); 
  setMotorY(CALIB_PWM);
}

// ====================== FUNGSI HOME (DIPERBAIKI) ======================
void startHomePosition() {
  if (isCalibrating) return;

  Serial.println("=== HOMING: Kembali ke (0,0) ===");
  Serial.printf("Posisi Saat Ini: X=%d Y=%d\n", position1_mm, position2_mm);

  // LOGIKA BARU: Hitung selisih untuk kembali ke 0
  // Jika posisi 100, move = -100. Jika posisi -50, move = 50.
  int moveX = 0 - position1_mm;
  int moveY = 0 - position2_mm;
  
  // Gerakkan X jika belum 0
  if (moveX != 0) {
    startMotorXMovement(moveX, 120);
  } else {
    setMotorX(0); // Pastikan berhenti
  }
  
  // Gerakkan Y jika belum 0
  if (moveY != 0) {
    startMotorYMovement(moveY, 150);
  } else {
    setMotorY(0); // Pastikan berhenti
  }
}

// ====================== I2C HANDLERS ======================
void receiveEvent(int howMany) {
  if (howMany < 1) return;
  received_cmd = Wire.read();

  if (howMany >= 3) {
    uint8_t high_byte = Wire.read();
    uint8_t low_byte = Wire.read();
    received_value = (high_byte << 8) | low_byte;
    Serial.printf("I2C RX (%d bytes) -> CMD: 0x%02X | Data: 0x%02X 0x%02X | Val: %d\n", howMany, received_cmd, high_byte, low_byte, received_value);
  } else if (howMany == 2) {
    received_value = Wire.read();
    Serial.printf("I2C RX (%d bytes) -> CMD: 0x%02X | Data: 0x%02X | Val: %d\n", howMany, received_cmd, received_value, received_value);
  } else {
    received_value = 0;
    Serial.printf("I2C RX (%d bytes) -> CMD: 0x%02X\n", howMany, received_cmd);
  }
  new_command = true;
}

void requestEvent() {
  int16_t x_mm = position1_mm;
  int16_t y_mm = position2_mm;

  positionData[0] = (x_mm >> 8) & 0xFF;
  positionData[1] = x_mm & 0xFF;
  positionData[2] = (y_mm >> 8) & 0xFF;
  positionData[3] = y_mm & 0xFF;

  Wire.write(positionData, 4);
}

// ====================== TASK CORE 0 (I2C) ======================
void TaskI2Ccode(void *pvParameters) {
  Wire.begin(I2C_ADDRESS);
  Wire.onReceive(receiveEvent);
  Wire.onRequest(requestEvent);
  Serial.println("Core 0: I2C Ready");

  for (;;) {
    vTaskDelay(10 / portTICK_PERIOD_MS);
  }
}

// ====================== TASK CORE 1 (LOGIKA MOTOR) ======================
void TaskMotorcode(void *pvParameters) {
  Serial.println("Core 1: Motor Logic Ready");
  
  unsigned long lastPrint = 0;
  unsigned long sampleTimer = 0;

  for (;;) {
    // 1. Update Posisi Realtime
    position1_mm = encoder1Count * MM_PER_COUNT;
    position2_mm = encoder2Count * MM_PER_COUNT;

    // 2. Eksekusi Perintah I2C
    if (new_command) {
      executeCommand(); // Fungsi ada di bawah
      new_command = false;
    }

    unsigned long currentMillis = millis();

    // ================= LOGIKA KALIBRASI INDEPENDEN =================
    if (isCalibrating) {
      
      // Cek encoder setiap 50ms (Sampling Rate)
      if (currentMillis - sampleTimer > 50) {
        sampleTimer = currentMillis;

        // --- CEK MOTOR X ---
        if (!calibXDone) {
          long currentEncX = encoder1Count;
          // Cek perubahan posisi
          if (abs(currentEncX - lastCalibEncX) > CALIB_TOLERANCE) { 
            lastMoveTimeX = currentMillis; // Masih gerak -> Reset timer
            lastCalibEncX = currentEncX;   
          }
          // Cek Stall
          if (currentMillis - lastMoveTimeX > STALL_TIME_MS) {
            setMotorX(0);       // STOP X
            calibXDone = true;  
            Serial.println(">>> X STOP (Mentok)");
          }
        }

        // --- CEK MOTOR Y ---
        if (!calibYDone) {
          long currentEncY = encoder2Count;
          // Cek perubahan posisi
          if (abs(currentEncY - lastCalibEncY) > CALIB_TOLERANCE) {
            lastMoveTimeY = currentMillis; // Masih gerak -> Reset timer
            lastCalibEncY = currentEncY;   
          }
          // Cek Stall
          if (currentMillis - lastMoveTimeY > STALL_TIME_MS) {
            setMotorY(0);       // STOP Y
            calibYDone = true;  
            Serial.println(">>> Y STOP (Mentok)");
          }
        }

        // --- JIKA KEDUANYA SELESAI ---
        if (calibXDone && calibYDone) {
          isCalibrating = false;
          // Reset Titik Nol
          encoder1Count = 0; encoder2Count = 0;
          position1_mm = 0; position2_mm = 0;
          setMotorX(0); setMotorY(0);
          Serial.println("=== KALIBRASI SELESAI (0,0) ===");
        }
      }
    }
    // ================= LOGIKA GERAK NORMAL (JARAK) =================
    else {
      // Logic X
      if (isMovingX) {
        long dist = (encoder1Count - initialEncoderCountX) * MM_PER_COUNT;
        // Gunakan abs() untuk menghandle gerak maju/mundur
        if (abs(dist) >= abs(targetDistanceX)) {
          setMotorX(0);
          isMovingX = false;
          Serial.println("Target X Tercapai");
        }
      }
      // Logic Y
      if (isMovingY) {
        long dist = (encoder2Count - initialEncoderCountY) * MM_PER_COUNT;
        // Gunakan abs() untuk menghandle gerak maju/mundur
        if (abs(dist) >= abs(targetDistanceY)) {
          setMotorY(0);
          isMovingY = false;
          Serial.println("Target Y Tercapai");
        }
      }
    }

    // Debug Print (0.5 detik sekali)
    if (millis() - lastPrint > 500) {
      if (isCalibrating) {
         Serial.printf("[CALIB] X:%s | Y:%s\n", 
           calibXDone ? "STOP" : "RUN ", calibYDone ? "STOP" : "RUN ");
      } else {
         Serial.printf("Pos: X=%d Y=%d | PWM X=%d Y=%d\n",
           position1_mm, position2_mm, motorXPWM, motorYPWM);
      }
      lastPrint = millis();
    }

    vTaskDelay(5 / portTICK_PERIOD_MS);
  }
}

// ====================== PARSING PERINTAH ======================
void executeCommand() {
  int pwmX = 120; // Default speed
  int pwmY = 180; 

  Serial.printf("CMD: 0x%02X Val: %d | Posisi Awal X:%d Y:%d\n", 
                received_cmd, received_value, position1_mm, position2_mm);

  switch (received_cmd) {
    case 0x01: // Maju (Y-) -> Gerak Relatif
      startMotorYMovement(-received_value, pwmY);
      break;

    case 0x02: // Mundur (Y+) -> Gerak Relatif
      startMotorYMovement(received_value, pwmY);
      break;

    case 0x03: // Kiri (X-) -> Gerak Relatif
      startMotorXMovement(received_value, pwmX);
      break;

    case 0x04: // Kanan (X+) -> Gerak Relatif
      startMotorXMovement(-received_value, pwmX);
      break;

    case 0x05: // KALIBRASI (Stall Detection)
      startCalibration();
      break;
      
    case 0x06: // HOMING (Balik ke 0,0)
      startHomePosition();
      break;

    case 0x20: // Reset Zero Manual
      encoder1Count = 0; encoder2Count = 0;
      position1_mm = 0; position2_mm = 0;
      setMotorX(0); setMotorY(0);
      isMovingX = false; isMovingY = false;
      isCalibrating = false;
      Serial.println("Manual Reset Zero");
      break;

    // === PERBAIKAN LOGIKA ABSOLUTE MOVE ===
    case 0x10: // Mata kiri (Gerak ke posisi X Absolute)
      {
        int targetPos = received_value; // Misal ingin ke 100mm
        int delta = targetPos - position1_mm; // Jika skrg 0, delta = 100. Jika skrg 150, delta = -50.
        Serial.printf("Mata Kiri Absolute: Target %d, Gerak %d\n", targetPos, delta);
        startMotorXMovement(delta, 100);
      }
      break;

    case 0x11: // Mata kanan (Gerak ke posisi X Absolute)
      {
        int targetPos = received_value;
        int delta = targetPos - position1_mm;
        Serial.printf("Mata Kanan Absolute: Target %d, Gerak %d\n", targetPos, delta);
        startMotorXMovement(delta, 100);
      }
      break;

    case 0x32: // Kontrol PWM LED
      {
        int pwm_val = constrain(received_value, 0, 255);
        analogWrite(LED1_PIN, pwm_val);
        analogWrite(LED2_PIN, pwm_val);
        Serial.printf("LED PWM diatur ke: %d\n", pwm_val);
      }
      break;
  }
  received_cmd = 0;
}

// ====================== SETUP ======================
void setup() {
  Serial.begin(115200);

  // Setup Pin Motor
  pinMode(MOTOR_X_L_PWM, OUTPUT);
  pinMode(MOTOR_X_R_PWM, OUTPUT);
  pinMode(MOTOR_Y_L_PWM, OUTPUT);
  pinMode(MOTOR_Y_R_PWM, OUTPUT);
  pinMode(EN, OUTPUT);
  digitalWrite(EN, HIGH); 

  // Setup Pin LED
  pinMode(LED1_PIN, OUTPUT);
  pinMode(LED2_PIN, OUTPUT);
  analogWrite(LED1_PIN, 0);
  analogWrite(LED2_PIN, 0);

  // Setup Pin Encoder
  pinMode(ENCODER1_A, INPUT_PULLUP);
  pinMode(ENCODER1_B, INPUT_PULLUP);
  pinMode(ENCODER2_A, INPUT_PULLUP);
  pinMode(ENCODER2_B, INPUT_PULLUP);

  // Setup Interrupt
  attachInterrupt(digitalPinToInterrupt(ENCODER1_A), encoder1ISR, RISING);
  attachInterrupt(digitalPinToInterrupt(ENCODER2_A), encoder2ISR, RISING);

  // Tasks
  xTaskCreatePinnedToCore(TaskI2Ccode, "TaskI2C", 4096, NULL, 1, &TaskI2C, 0);
  xTaskCreatePinnedToCore(TaskMotorcode, "TaskMotor", 4096, NULL, 1, &TaskMotor, 1);

  Serial.println("ESP32 Robot Ready. PWM Calib: 80, Timeout: 0.5s");
}

void loop() {
  // Kosong
}