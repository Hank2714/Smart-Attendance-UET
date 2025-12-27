#define F_CPU 8000000UL
#include <avr/io.h>
#include <util/delay.h>
#include <avr/interrupt.h>
#include <string.h>
#include "lcd_lib.h"

/* ================= UART ================= */
#define BAUD 9600
#define UBRR_VAL ((F_CPU/16/BAUD)-1)
#define BUFFER_SIZE 32

volatile char rx_buffer[BUFFER_SIZE];
volatile uint8_t rx_index = 0;
volatile uint8_t string_ready = 0;

/* ================= SENSOR ================= */
volatile uint8_t sensor_flag = 0;

/* ================= TIME ================= */
volatile uint16_t tick_10ms = 0;

/* ================= STATE ================= */
typedef enum {
    STATE_IDLE,
    STATE_CHECKING,
    STATE_RESULT_OK,
    STATE_RESULT_FAIL
} system_state_t;

volatile system_state_t state = STATE_IDLE;

/* ================= UART ================= */
void UART_Init(void) {
    UBRRL = (uint8_t)UBRR_VAL;
    UBRRH = (uint8_t)(UBRR_VAL >> 8);
    UCSRB = (1<<RXEN) | (1<<TXEN) | (1<<RXCIE);
    UCSRC = (1<<URSEL) | (1<<UCSZ1) | (1<<UCSZ0);
}

void UART_TxChar(char ch) {
    while (!(UCSRA & (1<<UDRE)));
    UDR = ch;
}

void UART_SendString(const char *s) {
    while (*s) UART_TxChar(*s++);
    UART_TxChar('\r');
    UART_TxChar('\n');
}

/* ================= INTERRUPTS ================= */
/*
 * PATCH 1:
 * - Ch? coi '\n' là k?t thúc dòng.
 * - B? qua '\r' ?? tránh t?o "chu?i r?ng" khi PC g?i "\r\n".
 */
ISR(USART_RXC_vect) {
    char c = UDR;

    if (c == '\r') {
        return; // ignore CR
    }

    if (c == '\n') {                 // only LF terminates
        rx_buffer[rx_index] = 0;
        rx_index = 0;
        string_ready = 1;
        return;
    }

    if (rx_index < BUFFER_SIZE - 1) {
        rx_buffer[rx_index++] = c;
    }
}

ISR(INT0_vect) {
    if (state == STATE_IDLE && sensor_flag == 0) {
        sensor_flag = 1;
    }
}

/* ================= TIMER 10ms ================= */
ISR(TIMER0_COMP_vect) {
    tick_10ms++;
}

void timer0_init(void) {
    TCCR0 = (1<<WGM01) | (1<<CS02) | (1<<CS00); // CTC, prescaler 1024
    OCR0 = 78;                                 // ~10ms @8MHz
    TIMSK |= (1<<OCIE0);
}

/* ================= MAIN ================= */
int main(void) {
    DDRC = 0xFF;
    DDRB = 0xFF;

    LCD4_init();
    UART_Init();

    DDRD &= ~(1<<PD2);
    PORTD |= (1<<PD2);
    MCUCR |= (1<<ISC01);
    GICR |= (1<<INT0);

    timer0_init();
    sei();

    LCD4_write_string((unsigned char*)"System Ready");

    uint16_t state_timer = 0;

    while (1) {
        /* ===== SENSOR ===== */
        if (sensor_flag) {
            sensor_flag = 0;
            state = STATE_CHECKING;
            state_timer = tick_10ms;

            // --- RESET toàn b? data c? ---
            cli();
            string_ready = 0;
            rx_index = 0;
            memset((char*)rx_buffer, 0, BUFFER_SIZE);
            sei();

            // --- G?i yêu c?u m?i ---
            UART_SendString("NG");
            UART_SendString("CK");
            LCD4_clear();
            LCD4_write_string((unsigned char*)"Checking...");

            state = STATE_CHECKING;
            state_timer = tick_10ms;
        }

        /* ===== UART ===== */
        if (string_ready) {
            /*
             * PATCH 2:
             * Copy l?nh sang buffer local trong vùng b?o v? ng?t
             * ?? tránh ISR ghi ?è rx_buffer khi main ?ang strcmp().
             */
            char cmd[BUFFER_SIZE];

            cli();
            string_ready = 0;
            strcpy(cmd, (char*)rx_buffer);
            rx_buffer[0] = 0; // clear (optional)
            sei();

            if (strcmp(cmd, "RUOK") == 0) {
                UART_SendString("CF");
            }
            else if (state == STATE_CHECKING && cmd[0] == 'T') {
                LCD4_clear();
                LCD4_write_string((unsigned char*)"Welcome");
                LCD4_gotoxy(2,1);
                LCD4_write_string((unsigned char*)(cmd+1));
                state = STATE_RESULT_OK;
                state_timer = tick_10ms;
            }
            else if (state == STATE_CHECKING && strcmp(cmd, "F") == 0) {
                LCD4_clear();
                LCD4_write_string((unsigned char*)"User not found");
                state = STATE_RESULT_FAIL;
                state_timer = tick_10ms;
            }
        }

        /* ===== TIMEOUTS ===== */
        if (state == STATE_CHECKING && (tick_10ms - state_timer) > 1500) {
            LCD4_clear();
            LCD4_write_string((unsigned char*)"User not found");
            state = STATE_RESULT_FAIL;
            state_timer = tick_10ms;
        }

        if ((state == STATE_RESULT_OK || state == STATE_RESULT_FAIL) &&
            (tick_10ms - state_timer) > 500) {
            LCD4_clear();
            LCD4_write_string((unsigned char*)"System Ready");
            UART_SendString("RD");
            state = STATE_IDLE;
        }
    }
}