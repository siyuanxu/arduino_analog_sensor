#ifndef MEGA_ADC_PROTOCOL_CONSTANTS_H
#define MEGA_ADC_PROTOCOL_CONSTANTS_H

#define MEGA_ADC_INFO_LINE                                                    \
  "#INFO {\"protocol_version\":1,\"firmware_version\":\"1.0.0\","       \
  "\"board_model\":\"MEGA_2560\",\"channel_count\":16,"                 \
  "\"channels\":[\"A0\",\"A1\",\"A2\",\"A3\",\"A4\",\"A5\","       \
  "\"A6\",\"A7\",\"A8\",\"A9\",\"A10\",\"A11\",\"A12\",\"A13\"," \
  "\"A14\",\"A15\"],\"adc_bits\":10,\"sample_interval_ms\":1000}"

#define MEGA_ADC_HEADER_LINE                                                  \
  "#HEADER device_time_s,A0_raw,A1_raw,A2_raw,A3_raw,A4_raw,A5_raw,"        \
  "A6_raw,A7_raw,A8_raw,A9_raw,A10_raw,A11_raw,A12_raw,A13_raw,A14_raw,"    \
  "A15_raw,adc_bits"

#endif
