/**
 * Simple Serial Debugger for Rock Pi S
 * 1. Prints ALL incoming data (Raw + ASCII).
 * 2. Auto-replies to Handshake.
 */

#include <boost/asio.hpp>
#include <iostream>
#include <string>
#include <vector>

// CONFIGURATION
const std::string SERIAL_PORT = "/dev/stm32"; 
const unsigned int BAUD_RATE = 115200;

class SerialDebug {
    boost::asio::io_context& ioc_;
    boost::asio::serial_port serial_;
    char rx_buffer_[1024];

public:
    SerialDebug(boost::asio::io_context& ioc) 
        : ioc_(ioc), serial_(ioc) 
    {
        connect();
    }

    void connect() {
        try {
            serial_.open(SERIAL_PORT);
            serial_.set_option(boost::asio::serial_port_base::baud_rate(BAUD_RATE));
            serial_.set_option(boost::asio::serial_port_base::character_size(8));
            serial_.set_option(boost::asio::serial_port_base::parity(boost::asio::serial_port_base::parity::none));
            serial_.set_option(boost::asio::serial_port_base::stop_bits(boost::asio::serial_port_base::stop_bits::one));

            std::cout << "--- DEBUGGER CONNECTED TO " << SERIAL_PORT << " ---" << std::endl;
            std::cout << "Waiting for data..." << std::endl;
            read_loop();
        } catch (std::exception& e) {
            std::cerr << "Connection Failed: " << e.what() << std::endl;
        }
    }

    void read_loop() {
        serial_.async_read_some(boost::asio::buffer(rx_buffer_),
            [this](boost::system::error_code ec, std::size_t length) {
                if (!ec) {
                    // 1. Print Raw Data to Terminal
                    std::cout.write(rx_buffer_, length);
                    std::cout.flush();

                    // 2. Check for Handshake in this chunk
                    std::string chunk(rx_buffer_, length);
                    if (chunk.find("CMD_HELLO") != std::string::npos) {
                        std::cout << "\n[Hit] CMD_HELLO detected! Sending ACK...\n";
                        write("ACK_READY"); 
                    }

                    read_loop();
                } else {
                    std::cerr << "\n[Error] Read failed. Exiting.\n";
                }
            });
    }

    void write(std::string msg) {
        boost::asio::async_write(serial_, boost::asio::buffer(msg),
            [](boost::system::error_code ec, std::size_t) {
                if(!ec) std::cout << "[Sent] ACK_READY\n";
            });
    }
};

int main() {
    boost::asio::io_context ioc;
    SerialDebug dbg(ioc);
    ioc.run();
    return 0;
}
