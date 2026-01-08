/**
 * Control Port Debugger (C++)
 * 1. Satisfies STM32 Handshake (Sends ACK_READY)
 * 2. Listens on Port 8766 (Control)
 * 3. PRINTS every command received from Base Station to Terminal
 */

#include <boost/asio.hpp>
#include <boost/beast.hpp>
#include <iostream>
#include <string>
#include <deque>
#include <memory>
#include <vector>

namespace beast = boost::beast;
namespace websocket = beast::websocket;
namespace asio = boost::asio;
using tcp = boost::asio::ip::tcp;

const std::string SERIAL_PORT = "/dev/stm32";
const int WS_PORT_CONTROL = 8766;

// --- SERIAL HANDLER ---
class SerialHandler {
    asio::serial_port serial_;
    char rx_buffer_[1024];
    std::string rx_stream_;

public:
    SerialHandler(asio::io_context& ioc) : serial_(ioc) {
        connect();
    }

    void connect() {
        try {
            serial_.open(SERIAL_PORT);
            serial_.set_option(asio::serial_port_base::baud_rate(115200));
            serial_.set_option(asio::serial_port_base::character_size(8));
            serial_.set_option(asio::serial_port_base::parity(asio::serial_port_base::parity::none));
            serial_.set_option(asio::serial_port_base::stop_bits(asio::serial_port_base::stop_bits::one));
            
            std::cout << "[Serial] Listening on " << SERIAL_PORT << " (Waiting for Handshake)..." << std::endl;
            do_read();
        } catch (std::exception& e) {
            std::cerr << "[Serial] Error: " << e.what() << std::endl;
        }
    }

    void do_read() {
        serial_.async_read_some(asio::buffer(rx_buffer_), 
            [this](boost::system::error_code ec, std::size_t length) {
                if (!ec) {
                    rx_stream_.append(rx_buffer_, length);

                    // Check for Handshake
                    size_t pos = rx_stream_.find("CMD_HELLO");
                    if (pos != std::string::npos) {
                        std::cout << "[Serial] Handshake Request Detected! Sending ACK..." << std::endl;
                        write("ACK_READY"); 
                        rx_stream_.erase(pos, 9);
                    }
                    
                    // Keep buffer small
                    if(rx_stream_.size() > 2048) rx_stream_.clear();
                    
                    do_read();
                }
            });
    }

    void write(std::string msg) {
        asio::async_write(serial_, asio::buffer(msg), [](boost::system::error_code, std::size_t){});
    }
};

// --- WEBSOCKET SESSION ---
class WSSession : public std::enable_shared_from_this<WSSession> {
    websocket::stream<tcp::socket> ws_;
    beast::flat_buffer buffer_;

public:
    WSSession(tcp::socket socket) : ws_(std::move(socket)) {}

    void run() {
        ws_.async_accept(beast::bind_front_handler(&WSSession::on_accept, shared_from_this()));
    }

    void on_accept(beast::error_code ec) {
        if(ec) return;
        std::cout << "[Control Port 8766] Client Connected!" << std::endl;
        do_read();
    }

    void do_read() {
        ws_.async_read(buffer_, beast::bind_front_handler(&WSSession::on_read, shared_from_this()));
    }

    void on_read(beast::error_code ec, std::size_t bytes_transferred) {
        if(ec == websocket::error::closed) {
             std::cout << "[Control Port] Client Disconnected." << std::endl;
             return;
        }
        if(ec) return;

        std::string msg = beast::buffers_to_string(buffer_.data());
        buffer_.consume(buffer_.size());

        // --- PRINT TO TERMINAL ---
        std::cout << "[RX Control] " << msg << std::endl;

        do_read();
    }
};

// --- SERVER ---
class TestServer {
    asio::io_context& ioc_;
    tcp::acceptor acceptor_;
    SerialHandler serial_;

public:
    TestServer(asio::io_context& ioc) 
        : ioc_(ioc), 
          acceptor_(ioc, tcp::endpoint(tcp::v4(), WS_PORT_CONTROL)),
          serial_(ioc)
    {
        do_accept();
    }

    void do_accept() {
        acceptor_.async_accept([this](beast::error_code ec, tcp::socket socket) {
            if (!ec) {
                std::make_shared<WSSession>(std::move(socket))->run();
            }
            do_accept();
        });
    }
};

int main() {
    try {
        asio::io_context ioc;
        TestServer server(ioc);
        std::cout << "--- CONTROL DEBUGGER STARTED ---" << std::endl;
        std::cout << "1. Connect Steam Deck to Port 8766" << std::endl;
        std::cout << "2. Check Terminal for JSON messages" << std::endl;
        ioc.run();
    } catch (std::exception& e) {
        std::cerr << "Fatal: " << e.what() << std::endl;
    }
    return 0;
}
