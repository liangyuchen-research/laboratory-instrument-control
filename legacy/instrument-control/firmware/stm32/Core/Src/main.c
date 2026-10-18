#include "stm32f1xx_hal.h"
void send_modbus_power_off(void);
#include "stm32f1xx_hal_uart.h"
#include <string.h>
#include <stdlib.h>

/* === User Configurable Parameters === */
float ON_TIME_MS = 0.5f;
float OFF_TIME_MS = 2.0f;
uint32_t TOTAL_CYCLES = 50;
uint16_t SET_VOLTAGE = 30;
uint32_t VOLTAGE_TIMEOUT_SEC = 0;
volatile uint8_t waiting_for_poweroff = 0;
uint32_t stop_wait_start_time = 0;
UART_HandleTypeDef huart1; // MODBUS UART
UART_HandleTypeDef huart2; // RPi UART

TIM_HandleTypeDef htim2;
volatile uint32_t current_cycle = 0;
volatile uint8_t is_on = 1;
void SystemClock_Config(void);
void Error_Handler(void);

void MX_TIM2_Init(void)
{
    __HAL_RCC_TIM2_CLK_ENABLE();
    htim2.Instance           = TIM2;
    htim2.Init.Prescaler     = 719;      // 72 MHz / (719 + 1) = 100 kHz, or 0.01 ms per timer count
    htim2.Init.CounterMode   = TIM_COUNTERMODE_UP;
    htim2.Init.Period        = (uint32_t)(ON_TIME_MS * 100) - 1;   // Convert milliseconds to timer counts
    htim2.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
    if (HAL_TIM_Base_Init(&htim2) != HAL_OK) Error_Handler();

    HAL_NVIC_SetPriority(TIM2_IRQn, 0, 0);
    HAL_NVIC_EnableIRQ(TIM2_IRQn);
}

void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  if (htim->Instance == TIM2)
  {
    if (is_on) {
      HAL_GPIO_WritePin(GPIOA, GPIO_PIN_1, GPIO_PIN_RESET);
      __HAL_TIM_SET_AUTORELOAD(htim, (uint32_t)(OFF_TIME_MS * 100) - 1);
      is_on = 0;
    } else {
      current_cycle++;
      if (current_cycle >= TOTAL_CYCLES) {
        HAL_TIM_Base_Stop_IT(htim);
        HAL_GPIO_WritePin(GPIOA, GPIO_PIN_1, GPIO_PIN_RESET);
      } else {
        HAL_GPIO_WritePin(GPIOA, GPIO_PIN_1, GPIO_PIN_SET);
        __HAL_TIM_SET_AUTORELOAD(htim, (uint32_t)(ON_TIME_MS * 100) - 1);
        is_on = 1;
      }
    }
    __HAL_TIM_SET_COUNTER(htim, 0);
  }
}

volatile uint8_t stop_flag = 1;
uint32_t voltage_start_time = 0;

char uart_buffer[64];
uint8_t uart_index = 0;
uint8_t rx_byte;

uint8_t led_on = 0;
uint32_t led_on_time = 0;
uint8_t  led_blue_on = 0;
uint32_t led_blue_on_time = 0;

volatile uint8_t need_modbus_action = 0;

static void MX_GPIO_Init(void);
static void MX_USART1_UART_Init(void);
static void MX_USART2_UART_Init(void);
void send_modbus_set_voltage(int voltage);
void send_modbus_power_on(void);
void send_modbus_power_off(void);
void Error_Handler(void);

void MX_GPIO_Init(void)
{
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();  // Enable the GPIOB peripheral clock

  GPIO_InitTypeDef GPIO_InitStruct = {0};

  // Configure output pins for the pulse and status signals
  GPIO_InitStruct.Pin = GPIO_PIN_1 | GPIO_PIN_6 | GPIO_PIN_7 | GPIO_PIN_9;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  HAL_GPIO_WritePin(GPIOA, GPIO_PIN_1 | GPIO_PIN_6 | GPIO_PIN_7 | GPIO_PIN_9, GPIO_PIN_RESET);

  // Configure PB10 as an external-interrupt input
  GPIO_InitStruct.Pin = GPIO_PIN_10;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_RISING;  // Alternatively use falling or both edges if the hardware requires it
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);  // Initialize the GPIOB pin

  // Enable the EXTI15_10 interrupt
  HAL_NVIC_SetPriority(EXTI15_10_IRQn, 0, 0);
  HAL_NVIC_EnableIRQ(EXTI15_10_IRQn);
}


void MX_USART1_UART_Init(void)
{
  __HAL_RCC_USART1_CLK_ENABLE();
  huart1.Instance = USART1;
  huart1.Init.BaudRate = 9600;
  huart1.Init.WordLength = UART_WORDLENGTH_8B;
  huart1.Init.StopBits = UART_STOPBITS_2;
  huart1.Init.Parity = UART_PARITY_NONE;
  huart1.Init.Mode = UART_MODE_TX_RX;
  huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart1.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart1) != HAL_OK) Error_Handler();
}

void MX_USART2_UART_Init(void)
{
  __HAL_RCC_USART2_CLK_ENABLE();
  huart2.Instance = USART2;
  huart2.Init.BaudRate = 9600;
  huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_2;
  huart2.Init.Parity = UART_PARITY_NONE;
  huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart2) != HAL_OK) Error_Handler();
}

uint16_t Modbus_CRC16(uint8_t *buf, uint8_t len);
void parse_condition_string(const char *input);
void handle_stop_command(void);
void reset_all_parameters(void);

int main(void)
{
  HAL_Init();
  SystemClock_Config();
  MX_GPIO_Init();
  MX_USART1_UART_Init();
  MX_TIM2_Init();
  MX_USART2_UART_Init();
  HAL_UART_Receive_IT(&huart2, &rx_byte, 1);

  while (1)
  {
    if (led_on && HAL_GetTick() - led_on_time >= 100) {
      HAL_GPIO_WritePin(GPIOA, GPIO_PIN_7, GPIO_PIN_RESET);
      led_on = 0;
    }
    /* Turn off the blue LED after 100 milliseconds */
    if (led_blue_on && HAL_GetTick() - led_blue_on_time >= 100) {
    	HAL_GPIO_WritePin(GPIOA, GPIO_PIN_6, GPIO_PIN_RESET);
         led_blue_on = 0;
    }


    if (voltage_start_time > 0 && !stop_flag && VOLTAGE_TIMEOUT_SEC > 0) {
      uint32_t elapsed_ms = HAL_GetTick() - voltage_start_time;
      if (elapsed_ms > VOLTAGE_TIMEOUT_SEC * 1000UL) {
        handle_stop_command();
      }
    }

    if (need_modbus_action) {
      send_modbus_set_voltage(SET_VOLTAGE);
      HAL_Delay(300);
      send_modbus_power_on();
      HAL_Delay(1000);
      need_modbus_action = 0;
    }

    // Stop the active pulse sequence
    if (waiting_for_poweroff && HAL_GetTick() - stop_wait_start_time >= 2000) {
      send_modbus_power_off();
      waiting_for_poweroff = 0;
      reset_all_parameters();
    }
  }
}
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance == USART2)
  {
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_7, GPIO_PIN_SET);
    led_on = 1;
    led_on_time = HAL_GetTick();

    if (rx_byte == '\n') {
      uart_buffer[uart_index] = '\0';
      if (strncmp(uart_buffer, "stop", 4) == 0)
        handle_stop_command();
      else
        parse_condition_string(uart_buffer);
      uart_index = 0;
    } else {
      if (uart_index < sizeof(uart_buffer) - 1) {
        uart_buffer[uart_index++] = rx_byte;
      } else {
        uart_index = 0; // reset on overflow
      }
    }
    HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
  }
}

void parse_condition_string(const char *input)
{
  if (strncmp(input, "condition", 9) != 0 || strlen(input) < 31) return;

  char on_main[4] = {0}, on_sub[4] = {0}, off[5] = {0}, cycles[5] = {0}, volt[5] = {0}, timeout[5] = {0};
  strncpy(on_main, &input[9], 3);
  strncpy(on_sub,  &input[12], 3);
  strncpy(off,      &input[15], 4);
  strncpy(cycles,   &input[19], 4);
  strncpy(volt,     &input[23], 4);
  strncpy(timeout,  &input[27], 4);

  float on_time = atof(on_main) + atof(on_sub) / 100.0f;
  float off_time = atof(off);
  int voltage = atoi(volt);
  int cycle = atoi(cycles);
  int timeout_sec = atoi(timeout);

  ON_TIME_MS = on_time;
  OFF_TIME_MS = off_time;
  SET_VOLTAGE = voltage;
  TOTAL_CYCLES = cycle;
  VOLTAGE_TIMEOUT_SEC = timeout_sec;
  stop_flag = 0;
  current_cycle = 0;
  voltage_start_time = 0;

  need_modbus_action = 1;
}

void handle_stop_command(void)
{
  stop_flag = 1;
  HAL_TIM_Base_Stop_IT(&htim2);  // Stop the timer
  HAL_GPIO_WritePin(GPIOA, GPIO_PIN_1, GPIO_PIN_RESET);  // Set the pulse output low
  waiting_for_poweroff = 1;  // Begin the delayed power-off interval
  stop_wait_start_time = HAL_GetTick();  // Record the starting tick
}


void reset_all_parameters(void)
{
  ON_TIME_MS = 0.5f;
  OFF_TIME_MS = 2.0f;
  TOTAL_CYCLES = 50;
  SET_VOLTAGE = 30;
  VOLTAGE_TIMEOUT_SEC = 0;
  current_cycle = 0;
  voltage_start_time = 0;
  uart_index = 0;
}

void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
	if (GPIO_Pin == GPIO_PIN_10 && !stop_flag){
    voltage_start_time = HAL_GetTick();
    HAL_TIM_Base_Start_IT(&htim2);
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_1, GPIO_PIN_SET);

    /* Turn on the blue LED and record the starting tick */
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_6, GPIO_PIN_SET);
    led_blue_on      = 1;
    led_blue_on_time = HAL_GetTick();

    current_cycle = 0;
    is_on = 1;
    __HAL_TIM_SET_AUTORELOAD(&htim2, (uint32_t)(ON_TIME_MS  * 100) - 1);
    __HAL_TIM_SET_COUNTER(&htim2, 0);
  }
}


void EXTI15_10_IRQHandler(void)
{
  HAL_GPIO_EXTI_IRQHandler(GPIO_PIN_10);
}

void send_modbus_set_voltage(int voltage)
{
  uint16_t value = (uint16_t)(voltage*10);
  uint8_t cmd[8] = {0x01, 0x06, 0x00, 0x00,
                    (value >> 8) & 0xFF,
                    value & 0xFF,
                    0x00, 0x00};
  uint16_t crc = Modbus_CRC16(cmd, 6);
  cmd[6] = crc & 0xFF;
  cmd[7] = (crc >> 8) & 0xFF;
  HAL_UART_Transmit(&huart1, cmd, 8, HAL_MAX_DELAY);
}

void send_modbus_power_on(void)
{
  uint8_t cmd[8] = {0x01, 0x06, 0x00, 0x04, 0x00, 0x01, 0x00, 0x00};
  uint16_t crc = Modbus_CRC16(cmd, 6);
  cmd[6] = crc & 0xFF;
  cmd[7] = (crc >> 8) & 0xFF;
  HAL_UART_Transmit(&huart1, cmd, 8, HAL_MAX_DELAY);
}

void send_modbus_power_off(void)
{
  uint8_t cmd[8] = {0x01, 0x06, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00};
  uint16_t crc = Modbus_CRC16(cmd, 6);
  cmd[6] = crc & 0xFF;
  cmd[7] = (crc >> 8) & 0xFF;
  HAL_UART_Transmit(&huart1, cmd, 8, HAL_MAX_DELAY);
}

uint16_t Modbus_CRC16(uint8_t *buf, uint8_t len)
{
  uint16_t crc = 0xFFFF;
  for (int pos = 0; pos < len; pos++) {
    crc ^= (uint16_t)buf[pos];
    for (int i = 0; i < 8; i++) {
      if (crc & 0x0001) {
        crc >>= 1;
        crc ^= 0xA001;
      } else {
        crc >>= 1;
      }
    }
  }
  return crc;
}

void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.HSEPredivValue = RCC_HSE_PREDIV_DIV1;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLMUL = RCC_PLL_MUL9;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK) Error_Handler();

  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK |
                                RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;
  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK) Error_Handler();
}

void Error_Handler(void)
{
  __disable_irq();
  while (1) {}
}
