#include "mega_adc_core.h"
#include "protocol_constants.h"

#if !defined(ARDUINO_AVR_MEGA2560) || !defined(__AVR_ATmega2560__)
#error "This firmware must be compiled for Arduino Mega 2560."
#endif

namespace {

const uint8_t kAnalogPins[mega_adc::kChannelCount] = {
    A0, A1, A2, A3, A4, A5, A6, A7,
    A8, A9, A10, A11, A12, A13, A14, A15};

mega_adc::MillisExtender g_millis_extender;
mega_adc::PeriodicSchedule g_schedule(mega_adc::kSampleIntervalMs);
mega_adc::CommandParser g_command_parser;
mega_adc::ResponseRateLimiter g_response_limiter;

uint64_t extendedMillis() {
  return g_millis_extender.extend(millis());
}

void printUnsigned64(uint64_t value) {
  char digits[20];
  uint8_t count = 0;
  do {
    digits[count++] = static_cast<char>('0' + (value % 10));
    value /= 10;
  } while (value != 0);
  while (count != 0) {
    Serial.write(digits[--count]);
  }
}

void printDeviceTime(uint64_t elapsed_ms) {
  printUnsigned64(elapsed_ms / 1000);
  Serial.write('.');
  const uint16_t fraction = static_cast<uint16_t>(elapsed_ms % 1000);
  Serial.write(static_cast<uint8_t>('0' + (fraction / 100)));
  Serial.write(static_cast<uint8_t>('0' + ((fraction / 10) % 10)));
  Serial.write(static_cast<uint8_t>('0' + (fraction % 10)));
}

void sendInfo() {
  Serial.println(F(MEGA_ADC_INFO_LINE));
}

void sendHeader() {
  Serial.println(F(MEGA_ADC_HEADER_LINE));
}

void sendSample(uint64_t frame_time_ms) {
  uint16_t raw[mega_adc::kChannelCount];
  for (uint8_t channel = 0; channel < mega_adc::kChannelCount; ++channel) {
    raw[channel] = static_cast<uint16_t>(analogRead(kAnalogPins[channel]));
  }

  printDeviceTime(frame_time_ms);
  for (uint8_t channel = 0; channel < mega_adc::kChannelCount; ++channel) {
    Serial.write(',');
    Serial.print(raw[channel]);
  }
  Serial.write(',');
  Serial.println(mega_adc::kAdcBits);
}

void handleCommand(mega_adc::CommandEvent event, uint64_t now_ms) {
  if (event == mega_adc::kPending || event == mega_adc::kIgnored) {
    return;
  }
  if (!g_response_limiter.allow(now_ms)) {
    return;
  }
  if (event == mega_adc::kInfo) {
    sendInfo();
  } else if (event == mega_adc::kHeader) {
    sendHeader();
  } else {
    Serial.println(F("#ERROR UNKNOWN_COMMAND"));
  }
}

void processSerialInput() {
  uint8_t processed = 0;
  while (Serial.available() > 0 && processed < mega_adc::kMaxRxBytesPerLoop) {
    const char input = static_cast<char>(Serial.read());
    ++processed;
    handleCommand(g_command_parser.push(input), extendedMillis());
  }
}

}  // namespace

void setup() {
  const uint64_t start_ms = extendedMillis();
  g_schedule.start(start_ms);

  for (uint8_t channel = 0; channel < mega_adc::kChannelCount; ++channel) {
    pinMode(kAnalogPins[channel], INPUT);
  }

  Serial.begin(115200);
  sendInfo();
  sendHeader();
}

void loop() {
  processSerialInput();

  // Capture the timestamp before starting this frame's sequential ADC reads.
  const uint64_t frame_time_ms = extendedMillis();
  if (g_schedule.takeIfDue(frame_time_ms)) {
    sendSample(frame_time_ms);
  }
}
