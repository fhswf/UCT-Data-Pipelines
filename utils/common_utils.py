import logging
from logging.handlers import TimedRotatingFileHandler
import os
import sys
import traceback
import smtplib
import atexit

class EmailErrorHandler(logging.Handler):
    """
    Email handler for the logging module.
    """
    def __init__(self, level=logging.ERROR):
        super().__init__(level)
        self.smtp_host = os.getenv('SMTP_SERVER')
        self.smtp_port = int(os.getenv('SMTP_PORT'))
        self.from_addr = os.getenv('SMTP_FROM')
        self.to_addrs = os.getenv('EMAIL_RECEIVER').split(',')
        self.smtp_user = os.getenv('SMTP_USER')
        self.smtp_pass = os.getenv('SMTP_PASSWORD')
        self.use_tls = os.getenv('SMTP_TLS').lower() == 'true'
        self.sent_email = False
        self.error_detected = False
        self.error_message = None

        atexit.register(self.send_email_at_exit)
    
    def emit(self, record):
        if record.levelno >= self.level and not self.error_detected:
            self.error_detected = True
            self.logger_name = record.name if record.name else 'root'
            self.error_message = record.getMessage()
            if record.exc_info:
                self.error_message += "\n" + ''.join(traceback.format_exception(*record.exc_info))
    
    def send_email_at_exit(self):
        if not self.error_detected:
            return
            
        log_file_path = os.path.join('logs', f"{self.logger_name}.log")
        
        if os.path.exists(log_file_path):
            with open(log_file_path, 'r', encoding='utf-8') as f:
                log_content = f.read()
            
            subject = f"[FEHLER] {self.logger_name.capitalize()}: {self.error_message}"
            self._send_email(subject, self.logger_name, log_content)
    
    def _send_email(self, subject, logger_name, body):
        to_header = ', '.join(self.to_addrs)
        message = f"From: {self.from_addr}\r\n"
        message += f"To: {to_header}\r\n"
        message += f"Subject: {subject}\r\n\r\n"
        message += f"Fehler beim Ausführen der {logger_name}-Pipeline\r\n"
        message += "============================================\n\n"
        message += body

        try:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port) as server:
                if self.smtp_user and self.smtp_pass:
                    server.login(self.smtp_user, self.smtp_pass)
                server.sendmail(self.from_addr, self.to_addrs, message.encode('utf-8'))
        except Exception as e:
            print(f"Fehler beim E-Mail-Versand: {e}")

def setup_logger(logfile : str, logname : str = None, level: int = 10, use_email: bool = False):
    """
    Initialize and configure a logger.

    Args:
        logfile (str): Path to the log file.
        logname (str): Name of the logger.
        level (int): Logging level (e.g., logging.DEBUG (10), logging.INFO (20)). Default is 10.
    """
    logger = logging.getLogger(logname)
    logger.setLevel(level)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    handlers = [
        TimedRotatingFileHandler(logfile, when="midnight", interval=1, backupCount=365, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
        logging.StreamHandler(sys.stderr),
        logging.StreamHandler()
    ]
    
    for handler in handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # E-Mail-Handler
    if use_email:
        email_handler = EmailErrorHandler()
        email_handler.setFormatter(formatter)
        logger.addHandler(email_handler)

    sys.excepthook = log_exception # log stderr 
    return logger

def log_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        # Let Ctrl+C interrupts pass through
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logging.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

def get_env_vars(varlist):
    return [os.getenv(var) for var in varlist]