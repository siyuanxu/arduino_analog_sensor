void setup ()
  {
  Serial.begin (9600);
  }  // end of setup

void loop ()
  {
  for (int whichPort = A0; whichPort <= A5; whichPort++)
     {
//     Serial.print ("Analog port = ");
//     Serial.print (whichPort);
 //    analogRead (whichPort);  // dummy read
     int result = analogRead (whichPort);
//     Serial.print (", result = ");
     Serial.println (result);
     delay(10);
     } 
  Serial.println (-1);
  delay (100);
  }  // end of loop
