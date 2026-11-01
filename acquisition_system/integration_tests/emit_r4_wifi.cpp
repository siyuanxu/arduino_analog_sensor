// SIMULATED fixture using the actual firmware formatter, never hardware data.
#include <cstdio>
#include "../firmware/r4_adc/r4_adc_protocol.h"

int main() {
  char info[224];
  r4adc::formatInfoLine(info, sizeof(info), "UNO_R4_WIFI");
  std::fputs(info, stdout);
  std::fputs(r4adc::kHeaderLine, stdout);
  const uint16_t raw[r4adc::kChannelCount] = {
      104, 204, 4096, 8192, 12288, 16383};
  const uint16_t filtered[2] = {102, 202};
  const uint16_t window0[r4adc::kFilterWindowSize] = {100, 101, 102, 103,
                                                       104};
  const uint16_t window1[r4adc::kFilterWindowSize] = {200, 201, 202, 203,
                                                       204};
  char frame[192];
  r4adc::formatDataFrame(frame, sizeof(frame), UINT64_C(1000), raw, filtered,
                         window0, window1);
  std::fputs(frame, stdout);
}
