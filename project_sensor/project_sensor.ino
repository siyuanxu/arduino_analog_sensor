#include <HX711.h>

HX711 scale(A1,A0);

void setup() {
    Serial.begin(38400);
}

void loop() {
    Serial.println(scale.read());
}
