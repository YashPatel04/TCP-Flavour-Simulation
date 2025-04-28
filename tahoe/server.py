import socket
import random
import time

class TahoeServer:
    def __init__(self, port=2000, loss_rate=0.1):
        """
        Initialize the server with configurable packet loss rate
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('localhost', port))
        self.loss_rate = loss_rate
        print(f"Server started on port {port} with {loss_rate*100}% packet loss")

    def run(self):
        """
        Listen for incoming packets and respond with ACKs
        """
        while True:
            try:
                data, address = self.sock.recvfrom(1024)
                seq_num = int(data.decode())
                
                # Simulate packet loss
                if random.random() < self.loss_rate:
                    print(f"Simulating loss for packet {seq_num}")
                    continue
                    
                # Send ACK back to client
                ack_message = f"ACK:{seq_num}"
                print(f"Received packet {seq_num}, sending {ack_message}")
                self.sock.sendto(ack_message.encode(), address)
                
            except Exception as e:
                print(f"Error: {e}")
                break

if __name__ == "__main__":
    # You can adjust the loss rate when running experiments
    server = TahoeServer(loss_rate=0.1)
    server.run()