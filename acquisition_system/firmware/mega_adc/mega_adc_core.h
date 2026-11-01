#ifndef MEGA_ADC_CORE_H
#define MEGA_ADC_CORE_H

#include <stdint.h>
#include <string.h>

namespace mega_adc {

static const uint32_t kSampleIntervalMs = 1000;
static const uint8_t kAdcBits = 10;
static const uint8_t kChannelCount = 16;
static const uint8_t kMaxRxBytesPerLoop = 32;
static const uint8_t kMaxCommandLength = 16;
static const uint8_t kMaxResponsesPerSecond = 4;

class MillisExtender {
 public:
  MillisExtender() : last_raw_(0), epoch_(0), initialized_(false) {}

  uint64_t extend(uint32_t raw) {
    if (!initialized_) {
      last_raw_ = raw;
      initialized_ = true;
      return raw;
    }
    if (raw < last_raw_) {
      epoch_ += (UINT64_C(1) << 32);
    }
    last_raw_ = raw;
    return epoch_ + raw;
  }

 private:
  uint32_t last_raw_;
  uint64_t epoch_;
  bool initialized_;
};

class PeriodicSchedule {
 public:
  explicit PeriodicSchedule(uint32_t interval_ms)
      : interval_ms_(interval_ms), next_ms_(0), started_(false) {}

  void start(uint64_t now_ms) {
    next_ms_ = now_ms + interval_ms_;
    started_ = true;
  }

  bool takeIfDue(uint64_t now_ms) {
    if (!started_) {
      start(now_ms);
      return false;
    }
    if (now_ms < next_ms_) {
      return false;
    }

    const uint64_t elapsed_intervals =
        ((now_ms - next_ms_) / interval_ms_) + 1;
    next_ms_ += elapsed_intervals * interval_ms_;
    return true;
  }

  uint64_t nextMsForTest() const { return next_ms_; }

 private:
  uint32_t interval_ms_;
  uint64_t next_ms_;
  bool started_;
};

enum CommandEvent {
  kPending,
  kIgnored,
  kInfo,
  kHeader,
  kUnknown
};

class CommandParser {
 public:
  CommandParser() : length_(0), overflow_(false) { buffer_[0] = '\0'; }

  CommandEvent push(char input) {
    if (input == '\r') {
      return kPending;
    }
    if (input != '\n') {
      if (overflow_) {
        return kPending;
      }
      if (length_ >= kMaxCommandLength) {
        overflow_ = true;
        return kPending;
      }
      buffer_[length_++] = input;
      buffer_[length_] = '\0';
      return kPending;
    }

    if (overflow_) {
      reset();
      return kIgnored;
    }
    if (length_ == 0) {
      reset();
      return kIgnored;
    }

    CommandEvent event = kUnknown;
    if (strcmp(buffer_, "INFO") == 0) {
      event = kInfo;
    } else if (strcmp(buffer_, "HEADER") == 0 || strcmp(buffer_, "H") == 0) {
      event = kHeader;
    }
    reset();
    return event;
  }

 private:
  void reset() {
    length_ = 0;
    overflow_ = false;
    buffer_[0] = '\0';
  }

  char buffer_[kMaxCommandLength + 1];
  uint8_t length_;
  bool overflow_;
};

class ResponseRateLimiter {
 public:
  ResponseRateLimiter() : window_start_ms_(0), response_count_(0), started_(false) {}

  bool allow(uint64_t now_ms) {
    if (!started_ || now_ms - window_start_ms_ >= 1000) {
      window_start_ms_ = now_ms;
      response_count_ = 0;
      started_ = true;
    }
    if (response_count_ >= kMaxResponsesPerSecond) {
      return false;
    }
    ++response_count_;
    return true;
  }

 private:
  uint64_t window_start_ms_;
  uint8_t response_count_;
  bool started_;
};

}  // namespace mega_adc

#endif
