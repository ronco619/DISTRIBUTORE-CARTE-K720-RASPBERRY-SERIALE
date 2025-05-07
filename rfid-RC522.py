# rfid.py

import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522
import time
import logging


GPIO.setwarnings(False)
class RFIDReader:
    def __init__(self):
        self.reader = None

    def setup(self):
        logging.info("Inizializzazione del lettore RFID.py 20")
        try:
            GPIO.setwarnings(False)
            self.reader = SimpleMFRC522()
            logging.info("Lettore RFID inizializzato con successo")
            return True
        except Exception as e:
            logging.error(f"Errore nell'inizializzazione del lettore RFID: {str(e)}")
            return False

    def read_card(self):
        if not self.reader:
            logging.error("Lettore RFID non inizializzato")
            return None

        try:
            id, text = self.reader.read_no_block()
            if id:
                uid = format(id, '08X')[:8].upper()
                logging.info(f"Carta letta con successo. UID: {uid}")
                return uid
            # Rimuovi completamente il log "Nessuna carta rilevata"
            # else:
            #     logging.debug("Nessuna carta rilevata")
        except Exception as e:
            logging.error(f"Errore nella lettura della carta RFID: {str(e)}")
        return None
        

def test_rfid_reader():
    """
    Funzione di test per il lettore RFID.
    """
    reader = RFIDReader()
    if reader.setup():
        print("Test del lettore RFID in corso...")
        try:
            for _ in range(10):  # Prova a leggere per 10 secondi
                uid = reader.read_card()
                if uid:
                    print(f"Carta rilevata con successo. UID: {uid}")
                    break
                time.sleep(1)
            else:
                print("Nessuna carta rilevata durante il test.")
        finally:
            reader.cleanup()
    else:
        print("Impossibile inizializzare il lettore RFID per il test.")

if __name__ == "__main__":
    test_rfid_reader()