import sys
import os
import tkinter as tk
from tkinter import scrolledtext, ttk, messagebox, Canvas
from datetime import datetime
import threading
import time
import queue
import logging

# Configuriamo il logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Aggiungiamo percorsi aggiuntivi per i moduli
sys.path.append('/usr/local/lib/python3.11/dist-packages')
sys.path.append('/usr/lib/python3/dist-packages')
sys.path.append(os.path.dirname(os.path.abspath(__file__)))  # Aggiungi la directory corrente

# Gestiamo le importazioni che potrebbero fallire
try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    print("AVVISO: Modulo 'serial' non trovato. Installalo con 'pip install pyserial'.")

# Importa il modulo RFID solo se disponibile
try:
    from rfid import RFIDReader
    RFID_AVAILABLE = True
except ImportError:
    RFID_AVAILABLE = False
    print("AVVISO: Modulo RFID non trovato. Assicurati che 'rfid.py' sia nella stessa directory o che 'mfrc522' sia installato.")


class SerialCommandSender:
    def __init__(self, com_port, baud_rate=9600, log_callback=None, status_callback=None):
        self.com_port = com_port
        self.baud_rate = baud_rate
        self.loop_command1 = "02 30 30 00 02 41 50 03 12"
        self.loop_command2 = "05 30 30"
        self.invia_carta_command = "02 30 30 00 02 44 43 03 04"
        self.leggi_carta_command = "02 30 30 00 03 46 43 37 03 30"
        self.recupera_carta_command = "02 30 30 00 02 43 50 03 10"
        self.accetta_carta_command = "02 30 30 00 03 46 43 38 03 3F"

        # Definizione dei segnali e stati
        self.response_signals = {
            "READER_INITIAL": "02303000065346303031340317",          # Stato iniziale del lettore
            "CARD_DISPENSING": "0230300006534630383134031f",         # Carta in erogazione (transitorio)
            "CARD_AT_OUTLET": "02303000065346303031310312",          # Carta presente alla bocchetta
            "CARD_RETRIEVING": "02303000065346313031300312",         # Carta in fase di recupero dalla bocchetta
            "CARD_IN_POSITION": "02303000065346303031330310",        # Carta in posizione interna
            "CARD_RETRIEVED": "02303000065346303031320311",          # Dopo recupero carta
            "READER_READY": "02303000065346313031320310"             # Lettore pronto
        }
        
        # Risposte standard del loop da ignorare nel log
        self.standard_loop_responses = ["063030"]

        self.custom_command_queue = queue.Queue()
        self.ser_lock = threading.Lock()
        self.loop_thread = None
        self.loop_running = False
        self.ser = None
        self.log_callback = log_callback
        self.status_callback = status_callback

    def log_message(self, message):
        if self.log_callback:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_callback(f"[{timestamp}] {message}")

    def format_command(self, command):
        return bytes.fromhex(command)

    def send_command(self, command, retries=4, is_loop_command=False):
        with self.ser_lock:
            if self.ser and self.ser.is_open:
                for attempt in range(retries):
                    try:
                        formatted_command = self.format_command(command)
                        self.ser.write(formatted_command)
                        time.sleep(0.1)

                        response = self.ser.read_all()
                        
                        if response:
                            response_hex = response.hex()
                            
                            # Log solo se non è un comando di loop o se è un comando di loop ma vogliamo mostrarlo
                            if not is_loop_command or response_hex not in self.standard_loop_responses:
                                self.log_message(f"Comando inviato: {command}")
                                self.log_message(f"Risposta: {response_hex}")
                            
                            # Aggiorniamo lo stato in base alla risposta
                            if self.status_callback:
                                # Per ogni risposta conosciuta, verifichiamo se corrisponde
                                for status_name, signal in self.response_signals.items():
                                    if response_hex == signal:
                                        # Inviamo lo stato e un messaggio appropriato
                                        status_message = ""
                                        if status_name == "CARD_IN_POSITION":
                                            status_message = "CARTA RILEVATA IN POSIZIONE!"
                                            self.log_message(status_message)
                                        elif status_name == "CARD_RETRIEVED":
                                            status_message = "CARTA RECUPERATA!"
                                            self.log_message(status_message)
                                        elif status_name == "CARD_AT_OUTLET":
                                            status_message = "CARTA PRESENTE ALLA BOCCHETTA!"
                                            self.log_message(status_message)
                                        elif status_name == "CARD_DISPENSING":
                                            status_message = "CARTA IN EROGAZIONE..."
                                            self.log_message(status_message)
                                        elif status_name == "CARD_RETRIEVING":
                                            status_message = "RECUPERO CARTA DALLA BOCCHETTA..."
                                            self.log_message(status_message)
                                        
                                        self.status_callback(status_name, True, status_message)
                                        break
                                
                            return response_hex
                        else:
                            if not is_loop_command:
                                self.log_message(f"Nessuna risposta, ritento... ({attempt + 1}/{retries})")
                            time.sleep(0.1)
                    except (serial.SerialException, OSError) as e:
                        self.log_message(f"Errore: {str(e)}")
                        self.stop_loop()
                        return None
                    except ValueError as e:
                        self.log_message(f"Errore di formattazione: {str(e)}")
                
                if not is_loop_command:
                    self.log_message(f"Comando non riuscito dopo {retries} tentativi")
                return None
            else:
                self.log_message("Porta seriale non aperta")
                return None

    def send_repeated_command(self, command, repeat):
        for _ in range(repeat):
            self.custom_command_queue.put(command)

    def start_loop(self):
        if not self.loop_running:
            try:
                self.ser = serial.Serial(
                    port=self.com_port,
                    baudrate=self.baud_rate,
                    timeout=0.5
                )
                self.loop_running = True
                self.loop_thread = threading.Thread(target=self.run_loop)
                self.loop_thread.daemon = True
                self.loop_thread.start()
                self.log_message("Loop avviato")
                return True
            except serial.SerialException as e:
                self.log_message(f"Impossibile aprire la porta seriale: {str(e)}")
                return False

    def stop_loop(self):
        self.loop_running = False
        with self.ser_lock:
            if self.ser and self.ser.is_open:
                try:
                    self.ser.close()
                except:
                    pass
        if self.loop_thread:
            self.loop_thread.join(timeout=1.0)
        self.log_message("Loop fermato")

    def run_loop(self):
        while self.loop_running:
            # Invia il comando di loop ma non logga le risposte standard
            self.send_command(self.loop_command1, is_loop_command=True)
            if not self.loop_running:
                break
            
            while not self.custom_command_queue.empty() and self.loop_running:
                custom_command = self.custom_command_queue.get()
                self.send_command(custom_command)
                time.sleep(0.1)
            
            if not self.loop_running:
                break
            
            # Anche qui evitiamo di logare le risposte standard
            self.send_command(self.loop_command2, is_loop_command=True)
            time.sleep(0.1)

    def invia_carta(self):
        self.log_message("Invio carta...")
        self.send_repeated_command(self.invia_carta_command, 2)

    def leggi_carta(self):
        self.log_message("Lettura carta...")
        self.send_repeated_command(self.leggi_carta_command, 2)

    def recupera_carta(self):
        self.log_message("Recupero carta...")
        self.send_repeated_command(self.recupera_carta_command, 2)

    def accetta_carta(self):
        self.log_message("Accettazione carta...")
        self.send_repeated_command(self.accetta_carta_command, 2)


class LedIndicator(Canvas):
    def __init__(self, parent, size=30, **kwargs):
        Canvas.__init__(self, parent, width=size, height=size, **kwargs)
        self.size = size
        self.configure(highlightthickness=0, borderwidth=0, bg=parent["bg"])
        padding = size * 0.1
        self.create_oval(padding, padding, size-padding, size-padding, 
                         fill="grey", outline="black", width=1, tags="led")
        
    def set_status(self, active, color="green"):
        if active:
            self.itemconfig("led", fill=color)
        else:
            self.itemconfig("led", fill="grey")


class K720GUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Distributore Carte K720 FEFFO SOLUTION")
        self.root.geometry("1024x700")
        self.root.configure(bg="#f0f0f0")
        
        self.serial_sender = None
        self.available_ports = []
        self.status_leds = {}
        
        # RFID Reader
        self.rfid_reader = None
        self.rfid_thread = None
        self.rfid_running = False
        self.last_rfid_uid = None
        
        self.create_widgets()
        self.refresh_ports()
        
    def create_widgets(self):
        # Frame principale diviso in due colonne
        main_frame = tk.Frame(self.root, bg="#f0f0f0")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        # Colonna sinistra per i comandi
        left_frame = tk.Frame(main_frame, bg="#f0f0f0", width=500)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        # Colonna destra per i log
        right_frame = tk.Frame(main_frame, bg="#f0f0f0", width=500)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(10, 0))
        
        # Frame per la selezione della porta COM
        port_frame = tk.LabelFrame(left_frame, text="Configurazione", bg="#f0f0f0", font=("Arial", 12, "bold"))
        port_frame.pack(fill=tk.X, pady=(0, 20))
        
        tk.Label(port_frame, text="Porta COM:", bg="#f0f0f0", font=("Arial", 10)).grid(row=0, column=0, padx=10, pady=10, sticky="w")
        
        self.port_combobox = ttk.Combobox(port_frame, width=20, state="readonly")
        self.port_combobox.grid(row=0, column=1, padx=10, pady=10, sticky="w")
        
        refresh_ports_button = tk.Button(port_frame, text="Aggiorna Porte", command=self.refresh_ports, bg="#4CAF50", fg="white", font=("Arial", 10))
        refresh_ports_button.grid(row=0, column=2, padx=10, pady=10)
        
        # Frame per la connessione
        connection_frame = tk.Frame(port_frame, bg="#f0f0f0")
        connection_frame.grid(row=1, column=0, columnspan=3, padx=10, pady=10, sticky="w")
        
        self.connect_button = tk.Button(connection_frame, text="Connetti", command=self.connect, bg="#2196F3", fg="white", font=("Arial", 10), width=15)
        self.connect_button.pack(side=tk.LEFT, padx=(0, 10))
        
        self.disconnect_button = tk.Button(connection_frame, text="Disconnetti", command=self.disconnect, bg="#f44336", fg="white", font=("Arial", 10), width=15, state=tk.DISABLED)
        self.disconnect_button.pack(side=tk.LEFT)
        
        # Frame per i comandi del distributore
        commands_frame = tk.LabelFrame(left_frame, text="Comandi Distributore", bg="#f0f0f0", font=("Arial", 12, "bold"))
        commands_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 20))
        
        # Frame per gli indicatori di stato
        status_indicators_frame = tk.LabelFrame(commands_frame, text="Stato", bg="#f0f0f0", font=("Arial", 10, "bold"))
        status_indicators_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # Indicatori LED per vari stati
        led_indicators = [
            ("READER_INITIAL", "Lettore inizializzato", "blue"),
            ("CARD_DISPENSING", "Carta in erogazione", "yellow"),
            ("CARD_AT_OUTLET", "Carta alla bocchetta", "red"),
            ("CARD_RETRIEVING", "Recupero da bocchetta", "cyan"),
            ("CARD_IN_POSITION", "Carta in posizione", "green"),
            ("CARD_RETRIEVED", "Carta recuperata", "orange"),
            ("READER_READY", "Lettore pronto", "purple")
        ]
        
        # Creiamo i LED in una griglia 3x3
        for i, (status_id, label_text, color) in enumerate(led_indicators):
            row = i // 3
            col = i % 3
            
            indicator_frame = tk.Frame(status_indicators_frame, bg="#f0f0f0")
            indicator_frame.grid(row=row, column=col, padx=8, pady=5, sticky="w")
            
            led = LedIndicator(indicator_frame, size=20)
            led.pack(side=tk.LEFT, padx=3)
            
            tk.Label(indicator_frame, text=label_text, bg="#f0f0f0", font=("Arial", 9)).pack(side=tk.LEFT, padx=3)
            
            # Memorizza il LED e il suo colore per poterlo aggiornare più tardi
            self.status_leds[status_id] = {"led": led, "color": color}
        
        # Pulsanti per i comandi principali - ridotta l'altezza per evitare problemi di impaginazione
        button_width = 20
        button_height = 1
        button_font = ("Arial", 12, "bold")
        button_pady = 5  # Ridotto il padding verticale
        
        self.loop_button = tk.Button(commands_frame, text="ATTIVA LOOP", command=self.start_loop, bg="#673AB7", fg="white", font=button_font, width=button_width, height=button_height, state=tk.DISABLED)
        self.loop_button.pack(fill=tk.X, padx=20, pady=button_pady)
        
        self.invia_carta_button = tk.Button(commands_frame, text="INVIA CARTA", command=self.invia_carta, bg="#FF9800", fg="white", font=button_font, width=button_width, height=button_height, state=tk.DISABLED)
        self.invia_carta_button.pack(fill=tk.X, padx=20, pady=button_pady)
        
        self.leggi_carta_button = tk.Button(commands_frame, text="LEGGI CARTA", command=self.leggi_carta, bg="#009688", fg="white", font=button_font, width=button_width, height=button_height, state=tk.DISABLED)
        self.leggi_carta_button.pack(fill=tk.X, padx=20, pady=button_pady)
        
        self.recupera_carta_button = tk.Button(commands_frame, text="RECUPERA CARTA", command=self.recupera_carta, bg="#E91E63", fg="white", font=button_font, width=button_width, height=button_height, state=tk.DISABLED)
        self.recupera_carta_button.pack(fill=tk.X, padx=20, pady=button_pady)
        
        self.accetta_carta_button = tk.Button(commands_frame, text="ACCETTA CARTA", command=self.accetta_carta, bg="#3F51B5", fg="white", font=button_font, width=button_width, height=button_height, state=tk.DISABLED)
        self.accetta_carta_button.pack(fill=tk.X, padx=20, pady=button_pady)
        
        # Frame per il lettore RFID
        rfid_frame = tk.LabelFrame(left_frame, text="Lettore RFID", bg="#f0f0f0", font=("Arial", 12, "bold"))
        rfid_frame.pack(fill=tk.X, pady=(0, 20))
        
        # Stato RFID e LED
        rfid_status_frame = tk.Frame(rfid_frame, bg="#f0f0f0")
        rfid_status_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.rfid_led = LedIndicator(rfid_status_frame, size=20)
        self.rfid_led.pack(side=tk.LEFT, padx=5)
        
        self.rfid_status_label = tk.Label(rfid_status_frame, text="Lettore RFID non inizializzato", bg="#f0f0f0", font=("Arial", 10))
        self.rfid_status_label.pack(side=tk.LEFT, padx=5)
        
        # Ultimo UID letto
        uid_frame = tk.Frame(rfid_frame, bg="#f0f0f0")
        uid_frame.pack(fill=tk.X, padx=10, pady=5)
        
        tk.Label(uid_frame, text="Ultimo UID:", bg="#f0f0f0", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=5)
        
        self.uid_var = tk.StringVar()
        self.uid_var.set("Nessuna carta letta")
        uid_label = tk.Label(uid_frame, textvariable=self.uid_var, bg="#f0f0f0", font=("Arial", 10))
        uid_label.pack(side=tk.LEFT, padx=5)
        
        # Pulsanti RFID
        rfid_buttons_frame = tk.Frame(rfid_frame, bg="#f0f0f0")
        rfid_buttons_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.rfid_init_button = tk.Button(rfid_buttons_frame, text="INIZIALIZZA RFID", command=self.initialize_rfid, bg="#9C27B0", fg="white", font=("Arial", 10, "bold"))
        self.rfid_init_button.pack(side=tk.LEFT, padx=(0, 10), fill=tk.X, expand=True)
        
        self.rfid_start_button = tk.Button(rfid_buttons_frame, text="AVVIA LETTURA", command=self.start_rfid_reading, bg="#00BCD4", fg="white", font=("Arial", 10, "bold"), state=tk.DISABLED)
        self.rfid_start_button.pack(side=tk.LEFT, padx=(0, 10), fill=tk.X, expand=True)
        
        self.rfid_stop_button = tk.Button(rfid_buttons_frame, text="FERMA LETTURA", command=self.stop_rfid_reading, bg="#FF5722", fg="white", font=("Arial", 10, "bold"), state=tk.DISABLED)
        self.rfid_stop_button.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Area di log
        log_frame = tk.LabelFrame(right_frame, text="Log delle Operazioni", bg="#f0f0f0", font=("Arial", 12, "bold"))
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, font=("Consolas", 10))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Pulsante per pulire il log
        self.clear_log_button = tk.Button(log_frame, text="PULISCI LOG", command=self.clear_log, bg="#607D8B", fg="white", font=("Arial", 10, "bold"))
        self.clear_log_button.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        # Barra di stato
        self.status_var = tk.StringVar()
        self.status_var.set("Disconnesso")
        status_bar = tk.Label(self.root, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor=tk.W, font=("Arial", 10))
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Aggiorniamo lo stato del lettore RFID in base alla disponibilità
        if not RFID_AVAILABLE:
            self.rfid_status_label.config(text="Modulo RFID non disponibile")
            self.rfid_init_button.config(state=tk.DISABLED)
            self.log_message("Modulo RFID non trovato. Assicurati che il file rfid.py sia nella stessa directory.")
        else:
            self.log_message("Modulo RFID trovato. Premi 'INIZIALIZZA RFID' per configurare il lettore.")
        
        self.log_message("Applicazione avviata. Seleziona una porta COM per iniziare.")
    
    def refresh_ports(self):
        if not SERIAL_AVAILABLE:
            self.available_ports = ["Modulo serial non disponibile"]
            self.port_combobox['values'] = self.available_ports
            self.port_combobox.current(0)
            self.connect_button.config(state=tk.DISABLED)
            self.log_message("Modulo serial non disponibile. Installa con 'pip install pyserial'")
            return
            
        self.available_ports = [port.device for port in serial.tools.list_ports.comports()]
        if not self.available_ports:
            self.available_ports = ["Nessuna porta trovata"]
        
        self.port_combobox['values'] = self.available_ports
        if self.available_ports:
            self.port_combobox.current(0)
        
        self.log_message(f"Porte seriali disponibili: {', '.join(self.available_ports)}")
    
    def connect(self):
        selected_port = self.port_combobox.get()
        if selected_port == "Nessuna porta trovata":
            messagebox.showerror("Errore", "Nessuna porta seriale disponibile")
            return
        
        # Creiamo l'oggetto SerialCommandSender con il callback per i log e il callback per gli stati
        self.serial_sender = SerialCommandSender(
            selected_port, 
            log_callback=self.log_message,
            status_callback=self.update_status
        )
        self.status_var.set(f"Connesso a {selected_port}")
        
        # Abilitiamo i pulsanti pertinenti
        self.connect_button.config(state=tk.DISABLED)
        self.disconnect_button.config(state=tk.NORMAL)
        self.loop_button.config(state=tk.NORMAL)
        
        # Resettiamo tutti i LED
        for status_id in self.status_leds:
            self.update_status(status_id, False)
        
        self.log_message(f"Connesso alla porta {selected_port}")
    
    def disconnect(self):
        if self.serial_sender:
            if self.serial_sender.loop_running:
                self.serial_sender.stop_loop()
                self.loop_button.config(text="ATTIVA LOOP", bg="#673AB7")
            
            self.serial_sender = None
            
            # Disabilitiamo i pulsanti pertinenti
            self.connect_button.config(state=tk.NORMAL)
            self.disconnect_button.config(state=tk.DISABLED)
            self.loop_button.config(state=tk.DISABLED)
            self.invia_carta_button.config(state=tk.DISABLED)
            self.leggi_carta_button.config(state=tk.DISABLED)
            self.recupera_carta_button.config(state=tk.DISABLED)
            self.accetta_carta_button.config(state=tk.DISABLED)
            
            # Resettiamo tutti i LED
            for status_id in self.status_leds:
                self.update_status(status_id, False)
            
            self.status_var.set("Disconnesso")
            self.log_message("Disconnesso dalla porta seriale")
    
    def update_status(self, status_id, active, message=""):
        if status_id in self.status_leds:
            led_info = self.status_leds[status_id]
            led_info["led"].set_status(active, led_info["color"] if active else "grey")
            
            # Aggiorna anche la barra di stato per alcuni stati specifici
            if active and message:
                current_status = self.status_var.get().split(" - ")[0]  # Manteniamo solo la prima parte
                self.status_var.set(f"{current_status} - {message}")
            
            # Disattiviamo gli altri LED se necessario (stati mutuamente esclusivi)
            if active:
                # Stati che non possono coesistere
                # Per ora disattiviamo solo gli altri stati quando uno diventa attivo
                exclusive_groups = [
                    ["READER_INITIAL", "CARD_DISPENSING", "CARD_AT_OUTLET", 
                     "CARD_RETRIEVING", "CARD_IN_POSITION", "CARD_RETRIEVED", "READER_READY"]
                ]
                
                for group in exclusive_groups:
                    if status_id in group:
                        for other_status in group:
                            if other_status != status_id and other_status in self.status_leds:
                                self.status_leds[other_status]["led"].set_status(False)
    
    def start_loop(self):
        if not self.serial_sender:
            return
        
        if not self.serial_sender.loop_running:
            success = self.serial_sender.start_loop()
            if success:
                self.loop_button.config(text="FERMA LOOP", bg="#f44336")
                # Abilitiamo i pulsanti per i comandi
                self.invia_carta_button.config(state=tk.NORMAL)
                self.leggi_carta_button.config(state=tk.NORMAL)
                self.recupera_carta_button.config(state=tk.NORMAL)
                self.accetta_carta_button.config(state=tk.NORMAL)
        else:
            self.serial_sender.stop_loop()
            self.loop_button.config(text="ATTIVA LOOP", bg="#673AB7")
            # Disabilitiamo i pulsanti per i comandi
            self.invia_carta_button.config(state=tk.DISABLED)
            self.leggi_carta_button.config(state=tk.DISABLED)
            self.recupera_carta_button.config(state=tk.DISABLED)
            self.accetta_carta_button.config(state=tk.DISABLED)
            # Resettiamo tutti i LED
            for status_id in self.status_leds:
                self.update_status(status_id, False)
    
    def invia_carta(self):
        if self.serial_sender and self.serial_sender.loop_running:
            self.serial_sender.invia_carta()
    
    def leggi_carta(self):
        if self.serial_sender and self.serial_sender.loop_running:
            self.serial_sender.leggi_carta()
    
    def recupera_carta(self):
        if self.serial_sender and self.serial_sender.loop_running:
            self.serial_sender.recupera_carta()
    
    def accetta_carta(self):
        if self.serial_sender and self.serial_sender.loop_running:
            self.serial_sender.accetta_carta()
    
    # Funzioni per il lettore RFID
    def initialize_rfid(self):
        if not RFID_AVAILABLE:
            self.log_message("Il modulo RFID non è disponibile")
            return
        
        self.log_message("Inizializzazione del lettore RFID...")
        try:
            self.rfid_reader = RFIDReader()
            setup_success = self.rfid_reader.setup()
            
            if setup_success:
                self.rfid_status_label.config(text="Lettore RFID inizializzato")
                self.rfid_led.set_status(True, "blue")
                self.rfid_init_button.config(state=tk.DISABLED)
                self.rfid_start_button.config(state=tk.NORMAL)
                self.log_message("Lettore RFID inizializzato con successo")
            else:
                self.rfid_status_label.config(text="Errore nell'inizializzazione")
                self.rfid_led.set_status(False)
                self.log_message("Errore nell'inizializzazione del lettore RFID")
        except Exception as e:
            self.log_message(f"Errore nell'inizializzazione del lettore RFID: {str(e)}")
            self.rfid_status_label.config(text="Errore nell'inizializzazione")
            self.rfid_led.set_status(False)
    
    def start_rfid_reading(self):
        if not self.rfid_reader:
            self.log_message("Il lettore RFID non è inizializzato")
            return
        
        if self.rfid_running:
            self.log_message("La lettura RFID è già in corso")
            return
        
        self.rfid_running = True
        self.rfid_thread = threading.Thread(target=self.rfid_reading_loop)
        self.rfid_thread.daemon = True
        self.rfid_thread.start()
        
        self.rfid_start_button.config(state=tk.DISABLED)
        self.rfid_stop_button.config(state=tk.NORMAL)
        self.rfid_status_label.config(text="Lettura RFID in corso...")
        self.rfid_led.set_status(True, "green")
        self.log_message("Lettura RFID avviata")
    
    def stop_rfid_reading(self):
        if not self.rfid_running:
            return
        
        self.rfid_running = False
        if self.rfid_thread:
            self.rfid_thread.join(timeout=1.0)
        
        self.rfid_start_button.config(state=tk.NORMAL)
        self.rfid_stop_button.config(state=tk.DISABLED)
        self.rfid_status_label.config(text="Lettore RFID in standby")
        self.rfid_led.set_status(True, "blue")
        self.log_message("Lettura RFID fermata")
    
    def rfid_reading_loop(self):
        if not self.rfid_reader:
            return
        
        while self.rfid_running:
            uid = self.rfid_reader.read_card()
            if uid:
                if uid != self.last_rfid_uid:
                    self.last_rfid_uid = uid
                    self.uid_var.set(uid)
                    self.log_message(f"Carta RFID rilevata - UID: {uid}")
                    
                    # Cambia temporaneamente il colore del LED per indicare una lettura riuscita
                    self.rfid_led.set_status(True, "red")
                    self.root.after(500, lambda: self.rfid_led.set_status(True, "green"))
            
            time.sleep(0.1)  # Piccola pausa per evitare di consumare troppe risorse
    
    def log_message(self, message):
        self.log_text.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see(tk.END)  # Scroll alla fine
    
    def clear_log(self):
        self.log_text.delete(1.0, tk.END)
        self.log_message("Log pulito")

# Funzione principale
def main():
    root = tk.Tk()
    app = K720GUI(root)
    root.protocol("WM_DELETE_WINDOW", lambda: on_closing(root, app))
    root.mainloop()

def on_closing(root, app):
    if app.serial_sender and app.serial_sender.loop_running:
        app.serial_sender.stop_loop()
    
    if app.rfid_running:
        app.stop_rfid_reading()
    
    root.destroy()

if __name__ == "__main__":
    main()