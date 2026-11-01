#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../r4_adc_protocol.h"

namespace {

void addFive(r4adc::TrimmedMeanWindow *window, const uint16_t values[5]) {
  for (uint8_t index = 0; index < 5; ++index) {
    window->add(values[index]);
  }
}

void assertWindow(const r4adc::TrimmedMeanWindow &window,
                  const uint16_t expected[5]) {
  uint16_t actual[5];
  window.copyOldestToNewest(actual);
  for (uint8_t index = 0; index < 5; ++index) {
    assert(actual[index] == expected[index]);
  }
}

void testCommands() {
  r4adc::CommandParser parser;
  const char commands[] = "INFO\r\nHEADER\nH\nBAD\n12345678901234567890\n";
  const r4adc::Command expected[] = {
      r4adc::Command::kInfo, r4adc::Command::kHeader,
      r4adc::Command::kHeader, r4adc::Command::kUnknown,
      r4adc::Command::kUnknown};
  size_t result_index = 0;
  for (char byte : commands) {
    if (byte == '\0') {
      break;
    }
    const r4adc::Command result = parser.push(byte);
    if (result != r4adc::Command::kNone) {
      assert(result_index < sizeof(expected) / sizeof(expected[0]));
      assert(result == expected[result_index++]);
    }
  }
  assert(result_index == sizeof(expected) / sizeof(expected[0]));
}

void testMillisWrap() {
  r4adc::MillisExtender uptime;
  assert(uptime.extend(UINT32_C(0xfffffff0)) == UINT64_C(4294967280));
  assert(uptime.extend(UINT32_C(0x000003d8)) == UINT64_C(4294968280));
  assert(uptime.extend(UINT32_C(0x000003d9)) == UINT64_C(4294968281));
}

void testOutputSchedule() {
  r4adc::SampleSchedule schedule;
  schedule.begin(UINT32_C(0xffffff00));
  assert(!schedule.due(UINT32_C(0x000000f3)));  // 499 ms, across wrap.
  assert(schedule.due(UINT32_C(0x000000f4)));   // 500 ms.
  assert(!schedule.due(UINT32_C(0x000000f5)));
  assert(schedule.due(UINT32_C(0x000008c4)));   // Multiple periods: one due.
  assert(!schedule.due(UINT32_C(0x000008c4)));  // No dense catch-up.
  assert(!schedule.due(UINT32_C(0x00000ab7)));
  assert(schedule.due(UINT32_C(0x00000ab8)));
}

void testFilterSchedule() {
  r4adc::FilterSampleSchedule schedule;
  schedule.begin(UINT32_C(0xffffff00));
  assert(schedule.poll(UINT32_C(0xffffffc7)) ==
         r4adc::FilterSampleAction::kNotDue);  // 199 ms.
  assert(schedule.poll(UINT32_C(0xffffffc8)) ==
         r4adc::FilterSampleAction::kSample);  // 200 ms.
  assert(schedule.poll(UINT32_C(0x0000008f)) ==
         r4adc::FilterSampleAction::kNotDue);  // 399 ms total.
  assert(schedule.poll(UINT32_C(0x00000090)) ==
         r4adc::FilterSampleAction::kSample);  // 400 ms, across wrap.
  assert(schedule.poll(UINT32_C(0x00000220)) ==
         r4adc::FilterSampleAction::kResetAndSample);  // Missed a deadline.
  assert(schedule.poll(UINT32_C(0x00000220)) ==
         r4adc::FilterSampleAction::kNotDue);  // No catch-up at same time.
  assert(schedule.poll(UINT32_C(0x000002e8)) ==
         r4adc::FilterSampleAction::kSample);
}

void testWarmupAndRollingOrder() {
  r4adc::TrimmedMeanWindow window;
  for (uint16_t value = 10; value < 14; ++value) {
    window.add(value);
  }
  assert(!window.ready());
  assert(window.count() == 4);
  assert(window.latest() == 13);

  window.add(14);
  const uint16_t first[5] = {10, 11, 12, 13, 14};
  assert(window.ready());
  assertWindow(window, first);
  assert(window.filtered() == 12);

  window.add(15);
  const uint16_t rolled[5] = {11, 12, 13, 14, 15};
  assertWindow(window, rolled);
  assert(window.latest() == 15);
  assert(window.filtered() == 13);

  window.reset();
  assert(!window.ready());
  assert(window.count() == 0);
}

void testSpikeStepConstantAndRounding() {
  r4adc::TrimmedMeanWindow spike;
  const uint16_t spike_values[5] = {100, 101, 102, 103, 10000};
  addFive(&spike, spike_values);
  assert(spike.filtered() == 102);  // Isolated high spike is removed.

  r4adc::TrimmedMeanWindow step;
  const uint16_t transition[5] = {100, 100, 100, 200, 200};
  addFive(&step, transition);
  assert(step.filtered() == 133);  // 400 / 3 rounds to 133.
  step.add(200);
  assert(step.filtered() == 167);  // 500 / 3 rounds to 167.
  step.add(200);
  assert(step.filtered() == 200);  // Sustained step fully propagates.

  r4adc::TrimmedMeanWindow constant;
  const uint16_t constants[5] = {777, 777, 777, 777, 777};
  addFive(&constant, constants);
  assert(constant.filtered() == 777);
}

void testDualChannelIndependenceAndAdcLimits() {
  r4adc::TrimmedMeanWindow a0;
  r4adc::TrimmedMeanWindow a1;
  const uint16_t a0_values[5] = {0, 1, 2, 3, 16383};
  const uint16_t a1_values[5] = {16383, 12000, 11000, 10000, 0};
  for (uint8_t index = 0; index < 5; ++index) {
    a0.add(a0_values[index]);
    a1.add(a1_values[index]);
  }
  assert(a0.filtered() == 2);
  assert(a1.filtered() == 11000);
  assert(a0.latest() == 16383);
  assert(a1.latest() == 0);

  r4adc::TrimmedMeanWindow maximum;
  const uint16_t maximums[5] = {16383, 16383, 16383, 16383, 16383};
  addFive(&maximum, maximums);
  assert(maximum.filtered() == 16383);  // Sum uses 32 bits, no overflow.
}

void testFormatting() {
  char info[224];
  int info_length =
      r4adc::formatInfoLine(info, sizeof(info), "UNO_R4_MINIMA");
  assert(info_length > 0 && static_cast<size_t>(info_length) < sizeof(info));
  assert(static_cast<size_t>(info_length) < 256);
  assert(strncmp(info, "#INFO {", 7) == 0);
  assert(strstr(info, "\"protocol_version\":2") != nullptr);
  assert(strstr(info, "\"board_model\":\"UNO_R4_MINIMA\"") != nullptr);
  assert(strstr(info, "\"firmware_version\":\"1.2.0\"") != nullptr);
  assert(strstr(info, "\"sample_interval_ms\":500") != nullptr);
  assert(strstr(info, "\"filter\":\"trim5_200ms_A0_A1\"") != nullptr);
  info_length = r4adc::formatInfoLine(info, sizeof(info), "UNO_R4_WIFI");
  assert(info_length > 0 && static_cast<size_t>(info_length) < sizeof(info));
  assert(static_cast<size_t>(info_length) < 256);
  assert(strstr(info, "\"board_model\":\"UNO_R4_WIFI\"") != nullptr);

  const uint16_t raw[r4adc::kChannelCount] = {14, 24, 30, 40, 50, 60};
  const uint16_t filtered[2] = {12, 22};
  const uint16_t window0[5] = {10, 11, 12, 13, 14};
  const uint16_t window1[5] = {20, 21, 22, 23, 24};
  char frame[192];
  int length = r4adc::formatDataFrame(frame, sizeof(frame),
                                      UINT64_C(4294967280), raw, filtered,
                                      window0, window1);
  assert(length > 0 && static_cast<size_t>(length) < sizeof(frame));
  assert(strcmp(frame,
                "4294967.280,14,24,30,40,50,60,12,22,"
                "10;11;12;13;14,20;21;22;23;24,14\n") == 0);

  const uint16_t maximums[r4adc::kChannelCount] = {
      16383, 16383, 16383, 16383, 16383, 16383};
  const uint16_t maximum_filtered[2] = {16383, 16383};
  const uint16_t maximum_window[5] = {16383, 16383, 16383, 16383, 16383};
  length = r4adc::formatDataFrame(frame, sizeof(frame), UINT64_MAX, maximums,
                                  maximum_filtered, maximum_window,
                                  maximum_window);
  assert(length > 0 && static_cast<size_t>(length) < sizeof(frame));
}

void emitSimulatedFirmwareOutput() {
  char info[224];
  r4adc::formatInfoLine(info, sizeof(info), "UNO_R4_MINIMA");
  fputs(info, stdout);
  fputs(r4adc::kHeaderLine, stdout);

  const uint16_t raw0[r4adc::kChannelCount] = {14, 24, 30, 40, 50, 60};
  const uint16_t filtered0[2] = {12, 22};
  const uint16_t window00[5] = {10, 11, 12, 13, 14};
  const uint16_t window01[5] = {20, 21, 22, 23, 24};
  const uint16_t raw1[r4adc::kChannelCount] = {
      16383, 3, 4096, 8192, 12288, 16383};
  const uint16_t filtered1[2] = {16381, 2};
  const uint16_t window10[5] = {0, 16380, 16381, 16382, 16383};
  const uint16_t window11[5] = {16383, 0, 1, 2, 3};
  char frame[192];
  r4adc::formatDataFrame(frame, sizeof(frame), UINT64_C(4294967280), raw0,
                         filtered0, window00, window01);
  fputs(frame, stdout);
  r4adc::formatDataFrame(frame, sizeof(frame), UINT64_C(4294967780), raw1,
                         filtered1, window10, window11);
  fputs(frame, stdout);
}

}  // namespace

int main(int argc, char **argv) {
  testCommands();
  testMillisWrap();
  testOutputSchedule();
  testFilterSchedule();
  testWarmupAndRollingOrder();
  testSpikeStepConstantAndRounding();
  testDualChannelIndependenceAndAdcLimits();
  testFormatting();
  if (argc == 2 && strcmp(argv[1], "--emit") == 0) {
    emitSimulatedFirmwareOutput();
  } else {
    puts("host behavior tests passed");
  }
  return 0;
}
