#include <assert.h>
#include <stdint.h>

#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "../mega_adc_core.h"
#include "../protocol_constants.h"

namespace {

std::vector<std::string> split(const std::string& value, char delimiter) {
  std::vector<std::string> fields;
  std::stringstream stream(value);
  std::string field;
  while (std::getline(stream, field, delimiter)) {
    fields.push_back(field);
  }
  return fields;
}

mega_adc::CommandEvent feed(mega_adc::CommandParser& parser,
                            const std::string& text) {
  mega_adc::CommandEvent last = mega_adc::kPending;
  for (std::string::const_iterator it = text.begin(); it != text.end(); ++it) {
    const mega_adc::CommandEvent event = parser.push(*it);
    if (event != mega_adc::kPending) {
      last = event;
    }
  }
  return last;
}

void testProtocolConstants() {
  const std::string info = MEGA_ADC_INFO_LINE;
  const std::string header = MEGA_ADC_HEADER_LINE;
  assert(info.find("\"protocol_version\":1") != std::string::npos);
  assert(info.find("\"firmware_version\":\"1.0.0\"") != std::string::npos);
  assert(info.find("\"board_model\":\"MEGA_2560\"") != std::string::npos);
  assert(info.find("\"channel_count\":16") != std::string::npos);
  assert(info.find("\"adc_bits\":10") != std::string::npos);
  assert(info.find("\"sample_interval_ms\":1000") != std::string::npos);
  for (int channel = 0; channel < 16; ++channel) {
    const std::string name = "\"A" + std::to_string(channel) + "\"";
    assert(info.find(name) != std::string::npos);
  }

  const std::vector<std::string> columns = split(header.substr(8), ',');
  assert(columns.size() == 18);
  assert(columns.front() == "device_time_s");
  assert(columns.back() == "adc_bits");
  for (int channel = 0; channel < 16; ++channel) {
    assert(columns[channel + 1] == "A" + std::to_string(channel) + "_raw");
  }
}

void testMillisWrapAndSchedule() {
  mega_adc::MillisExtender extender;
  assert(extender.extend(UINT32_C(0xFFFFFF00)) == UINT64_C(0xFFFFFF00));
  assert(extender.extend(UINT32_C(0xFFFFFFF0)) == UINT64_C(0xFFFFFFF0));
  assert(extender.extend(UINT32_C(0x00000020)) == UINT64_C(0x100000020));
  assert(extender.extend(UINT32_C(0x00000420)) == UINT64_C(0x100000420));

  mega_adc::PeriodicSchedule schedule(1000);
  schedule.start(100);
  assert(!schedule.takeIfDue(1099));
  assert(schedule.takeIfDue(1100));
  assert(schedule.nextMsForTest() == 2100);
  assert(schedule.takeIfDue(4600));
  assert(schedule.nextMsForTest() == 5100);
  assert(!schedule.takeIfDue(4601));
  assert(schedule.takeIfDue(5100));
}

void testCommandBoundariesAndRate() {
  mega_adc::CommandParser parser;
  assert(feed(parser, "INFO\r\n") == mega_adc::kInfo);
  assert(feed(parser, "HEADER\n") == mega_adc::kHeader);
  assert(feed(parser, "H\n") == mega_adc::kHeader);
  assert(feed(parser, "OTHER\n") == mega_adc::kUnknown);
  assert(feed(parser, "12345678901234567\n") == mega_adc::kIgnored);
  assert(feed(parser, "INFO\n") == mega_adc::kInfo);
  assert(feed(parser, "\n") == mega_adc::kIgnored);

  mega_adc::ResponseRateLimiter limiter;
  for (int response = 0; response < 4; ++response) {
    assert(limiter.allow(50));
  }
  assert(!limiter.allow(50));
  assert(limiter.allow(1050));
}

void printSimulatedSample() {
  std::cout << MEGA_ADC_INFO_LINE << '\n';
  std::cout << MEGA_ADC_HEADER_LINE << '\n';
  std::cout << "1.000";
  for (int channel = 0; channel < 16; ++channel) {
    std::cout << ',' << channel * 64;
  }
  std::cout << ",10\n";
}

}  // namespace

int main(int argc, char** argv) {
  testProtocolConstants();
  testMillisWrapAndSchedule();
  testCommandBoundariesAndRate();
  if (argc == 2 && std::string(argv[1]) == "--sample") {
    printSimulatedSample();
    return 0;
  }
  std::cout << "host tests passed\n";
  return 0;
}
