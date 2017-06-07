void setup() {
  // set bdrate
  Serial.begin(9600);
}

void loop() {
  // read analog value from A0
  int V0 = analogRead(A0);
  // print analog data through serial port
  Serial.println(V0);
  // set the gap between two reads
  delay(1000);
}
