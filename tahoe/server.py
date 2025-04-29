import socket
import random
import time

class tahoeserver:
    def __init__(self, port=2000, loss_rate=0.1):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind(('localhost', port))
        self.loss_rate = loss_rate
        print(f"Server started on port {port} with {loss_rate*100}% packet loss")

    def run(self):
        """
        Listen for coming packets and respond with ACKs
        """
        while True:
            try:
                dat, add = self.s.recvfrom(1024)
                seq_num = int(dat.decode())
                
                # Simulate packet loss
                if random.random() < self.loss_rate:
                    print(f"Simulating loss for packet {seq_num}")
                    continue
                    
                # Send ACK back to client
                ack_message = f"ACK:{seq_num}"
                print(f"Received packet {seq_num}, sending {ack_message}")
                self.s.sendto(ack_message.encode(), add)
                
            except Exception as e:
                print(f"Error: {e}")
                break

if __name__ == "__main__":
    # please adjust the loss rate when doing experiments
    prcss = tahoeserver(8000, 0.1)
    prcss.run()