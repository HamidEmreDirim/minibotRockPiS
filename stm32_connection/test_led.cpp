#include <boost/asio.hpp>
#include <iostream>
#include <string>

// --- CONFIGURATION ---
const std::string SERIAL_PORT = "/dev/stm32";
const unsigned int BAUD_RATE = 115200;

using namespace boost::asio;

int main() {
    io_context ioc;
    serial_port serial(ioc);

    try {
        // 1. Connect to Serial Port
        serial.open(SERIAL_PORT);
        serial.set_option(serial_port_base::baud_rate(BAUD_RATE));
        serial.set_option(serial_port_base::character_size(8));
        serial.set_option(serial_port_base::parity(serial_port_base::parity::none));
        serial.set_option(serial_port_base::stop_bits(serial_port_base::stop_bits::one));

        std::cout << "--- LED TESTER CONNECTED [" << SERIAL_PORT << "] ---" << std::endl;
        std::cout << "Type '1' for ON, '0' for OFF (or 'q' to quit)." << std::endl;

        // 2. Main Input Loop
        std::string input;
        while (true) {
            std::cout << "> ";
            std::getline(std::cin, input);

            if (input == "q" || input == "quit") break;

            std::string command;
            
            // Map simple inputs to your Robot Protocol
            if (input == "1" || input == "on") {
                command = "{led=1}\n"; // Adjust this format to match your specific STM32 firmware
            } 
            else if (input == "0" || input == "off") {
                command = "{led=0}\n";
            } 
            else {
                std::cout << "Invalid. Use 1 or 0." << std::endl;
                continue;
            }

            // 3. Send to STM32
            write(serial, buffer(command));
            std::cout << "Sent: " << command.substr(0, command.length()-1) << std::endl;
        }

    } catch (std::exception& e) {
        std::cerr << "ERROR: " << e.what() << std::endl;
        std::cerr << "Make sure the bridge is NOT running when you use this tool." << std::endl;
    }

    return 0;
}
