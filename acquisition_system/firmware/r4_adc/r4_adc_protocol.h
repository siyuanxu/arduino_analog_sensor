#ifndef R4_ADC_PROTOCOL_H
#define R4_ADC_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace r4adc {

constexpr uint8_t kChannelCount = 6;
constexpr uint8_t kAdcBits = 14;
constexpr uint32_t kSampleIntervalMs = 500;
constexpr uint32_t kFilterSampleIntervalMs = 200;
constexpr uint8_t kFilterWindowSize = 5;
constexpr char kFirmwareVersion[] = "1.2.0";
constexpr char kFilterName[] = "trim5_200ms_A0_A1";
constexpr char kHeaderLine[] =
    "#HEADER device_time_s,A0_raw,A1_raw,A2_raw,A3_raw,A4_raw,A5_raw,"
    "A0_filtered,A1_filtered,A0_window,A1_window,adc_bits\n";
constexpr char kUnknownCommandLine[] = "#ERROR UNKNOWN_COMMAND\n";

enum class Command : uint8_t {
  kNone,
  kInfo,
  kHeader,
  kUnknown,
};

// Extends the 32-bit Arduino millis() counter across ordinary rollover. Call
// this regularly; as with millis() itself, a caller that does not run for more
// than one complete 49.7-day wrap cannot infer how many wraps were missed.
class MillisExtender {
 public:
  MillisExtender() : initialized_(false), previous_(0), wrap_base_(0) {}

  uint64_t extend(uint32_t current) {
    if (!initialized_) {
      initialized_ = true;
      previous_ = current;
      return current;
    }
    if (current < previous_) {
      wrap_base_ += (UINT64_C(1) << 32);
    }
    previous_ = current;
    return wrap_base_ + current;
  }

 private:
  bool initialized_;
  uint32_t previous_;
  uint64_t wrap_base_;
};

// Maintains a fixed-period output phase. If execution is late by multiple
// periods, one output is requested and missed deadlines are skipped.
class SampleSchedule {
 public:
  SampleSchedule() : previous_deadline_base_(0), initialized_(false) {}

  void begin(uint32_t now) {
    previous_deadline_base_ = now;
    initialized_ = true;
  }

  bool due(uint32_t now) {
    if (!initialized_) {
      begin(now);
      return false;
    }
    const uint32_t elapsed = now - previous_deadline_base_;
    if (elapsed < kSampleIntervalMs) {
      return false;
    }
    const uint32_t periods = elapsed / kSampleIntervalMs;
    previous_deadline_base_ += periods * kSampleIntervalMs;
    return true;
  }

 private:
  uint32_t previous_deadline_base_;
  bool initialized_;
};

enum class FilterSampleAction : uint8_t {
  kNotDue,
  kSample,
  kResetAndSample,
};

// Schedules one real A0/A1 pair every 200 ms. A delay of at least two full
// periods proves that at least one sample deadline was missed; in that case the
// rolling windows must be discarded and warmed with real samples again. No
// artificial catch-up samples are generated. Unsigned subtraction keeps this
// safe across the 32-bit millis() rollover.
class FilterSampleSchedule {
 public:
  FilterSampleSchedule() : previous_deadline_base_(0), initialized_(false) {}

  void begin(uint32_t now) {
    previous_deadline_base_ = now;
    initialized_ = true;
  }

  FilterSampleAction poll(uint32_t now) {
    if (!initialized_) {
      begin(now);
      return FilterSampleAction::kNotDue;
    }
    const uint32_t elapsed = now - previous_deadline_base_;
    if (elapsed < kFilterSampleIntervalMs) {
      return FilterSampleAction::kNotDue;
    }
    if (elapsed >= 2U * kFilterSampleIntervalMs) {
      previous_deadline_base_ = now;
      return FilterSampleAction::kResetAndSample;
    }
    previous_deadline_base_ += kFilterSampleIntervalMs;
    return FilterSampleAction::kSample;
  }

 private:
  uint32_t previous_deadline_base_;
  bool initialized_;
};

// Five-point rolling window. Once full, exactly one minimum and one maximum
// are removed; the remaining three integers are averaged and rounded to the
// nearest ADC integer.
class TrimmedMeanWindow {
 public:
  TrimmedMeanWindow() : next_(0), count_(0) {
    for (uint8_t index = 0; index < kFilterWindowSize; ++index) {
      samples_[index] = 0;
    }
  }

  void reset() {
    next_ = 0;
    count_ = 0;
  }

  void add(uint16_t sample) {
    samples_[next_] = sample;
    next_ = static_cast<uint8_t>((next_ + 1U) % kFilterWindowSize);
    if (count_ < kFilterWindowSize) {
      ++count_;
    }
  }

  bool ready() const { return count_ == kFilterWindowSize; }
  uint8_t count() const { return count_; }

  uint16_t latest() const {
    if (count_ == 0) {
      return 0;
    }
    const uint8_t index =
        static_cast<uint8_t>((next_ + kFilterWindowSize - 1U) %
                             kFilterWindowSize);
    return samples_[index];
  }

  void copyOldestToNewest(uint16_t output[kFilterWindowSize]) const {
    const uint8_t oldest = ready() ? next_ : 0;
    for (uint8_t index = 0; index < count_; ++index) {
      output[index] = samples_[(oldest + index) % kFilterWindowSize];
    }
    for (uint8_t index = count_; index < kFilterWindowSize; ++index) {
      output[index] = 0;
    }
  }

  uint16_t filtered() const {
    if (!ready()) {
      return latest();
    }
    uint32_t sum = samples_[0];
    uint16_t minimum = samples_[0];
    uint16_t maximum = samples_[0];
    for (uint8_t index = 1; index < kFilterWindowSize; ++index) {
      const uint16_t sample = samples_[index];
      sum += sample;
      if (sample < minimum) {
        minimum = sample;
      }
      if (sample > maximum) {
        maximum = sample;
      }
    }
    const uint32_t middle_sum = sum - minimum - maximum;
    return static_cast<uint16_t>((middle_sum + 1U) / 3U);
  }

 private:
  uint16_t samples_[kFilterWindowSize];
  uint8_t next_;
  uint8_t count_;
};

// Incremental, fixed-storage line parser. Commands longer than the storage
// limit are discarded through their terminating LF and produce one error.
class CommandParser {
 public:
  CommandParser() : length_(0), overflow_(false) { buffer_[0] = '\0'; }

  Command push(char byte) {
    if (byte == '\r') {
      return Command::kNone;
    }
    if (byte != '\n') {
      if (overflow_) {
        return Command::kNone;
      }
      if (length_ + 1 >= sizeof(buffer_)) {
        overflow_ = true;
        return Command::kNone;
      }
      buffer_[length_++] = byte;
      buffer_[length_] = '\0';
      return Command::kNone;
    }

    if (overflow_) {
      reset();
      return Command::kUnknown;
    }
    if (length_ == 0) {
      reset();
      return Command::kNone;
    }

    Command result = Command::kUnknown;
    if (strcmp(buffer_, "INFO") == 0) {
      result = Command::kInfo;
    } else if (strcmp(buffer_, "HEADER") == 0 || strcmp(buffer_, "H") == 0) {
      result = Command::kHeader;
    }
    reset();
    return result;
  }

 private:
  void reset() {
    length_ = 0;
    overflow_ = false;
    buffer_[0] = '\0';
  }

  char buffer_[16];
  size_t length_;
  bool overflow_;
};

inline int formatInfoLine(char *output, size_t output_size,
                          const char *board_model) {
  return snprintf(
      output, output_size,
      "#INFO {\"protocol_version\":2,\"firmware_version\":\"%s\","
      "\"board_model\":\"%s\",\"channel_count\":6,"
      "\"channels\":[\"A0\",\"A1\",\"A2\",\"A3\",\"A4\",\"A5\"],"
      "\"adc_bits\":14,\"sample_interval_ms\":%u,\"filter\":\"%s\"}\n",
      kFirmwareVersion, board_model,
      static_cast<unsigned int>(kSampleIntervalMs), kFilterName);
}

inline int formatDataFrame(char *output, size_t output_size,
                           uint64_t frame_time_ms,
                           const uint16_t raw[kChannelCount],
                           const uint16_t filtered[2],
                           const uint16_t window0[kFilterWindowSize],
                           const uint16_t window1[kFilterWindowSize]) {
  // The R4's embedded printf does not handle %llu. Format the seconds
  // separately so all ADC arguments are consumed at their proper positions.
  char seconds_buffer[21];
  char *seconds_text = seconds_buffer + sizeof(seconds_buffer) - 1;
  *seconds_text = '\0';
  uint64_t seconds = frame_time_ms / 1000U;
  do {
    *--seconds_text = static_cast<char>('0' + seconds % 10U);
    seconds /= 10U;
  } while (seconds != 0);
  const unsigned int milliseconds =
      static_cast<unsigned int>(frame_time_ms % 1000U);
  return snprintf(
      output, output_size,
      "%s.%03u,%u,%u,%u,%u,%u,%u,%u,%u,"
      "%u;%u;%u;%u;%u,%u;%u;%u;%u;%u,14\n",
      seconds_text, milliseconds, static_cast<unsigned int>(raw[0]),
      static_cast<unsigned int>(raw[1]), static_cast<unsigned int>(raw[2]),
      static_cast<unsigned int>(raw[3]), static_cast<unsigned int>(raw[4]),
      static_cast<unsigned int>(raw[5]),
      static_cast<unsigned int>(filtered[0]),
      static_cast<unsigned int>(filtered[1]),
      static_cast<unsigned int>(window0[0]),
      static_cast<unsigned int>(window0[1]),
      static_cast<unsigned int>(window0[2]),
      static_cast<unsigned int>(window0[3]),
      static_cast<unsigned int>(window0[4]),
      static_cast<unsigned int>(window1[0]),
      static_cast<unsigned int>(window1[1]),
      static_cast<unsigned int>(window1[2]),
      static_cast<unsigned int>(window1[3]),
      static_cast<unsigned int>(window1[4]));
}

}  // namespace r4adc

#endif  // R4_ADC_PROTOCOL_H
