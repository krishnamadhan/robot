module.exports = {
  apps: [
    {
      name: 'cosmo',
      script: 'tools/cosmo_demo.py',
      interpreter: 'python3',
      interpreter_args: '-u',
      cwd: '/home/pi/robot',
      watch: false,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 20,
      max_memory_restart: '3000M',
      kill_timeout: 5000,
      env: {
        PYTHONPATH: '/home/pi/robot',
        // Allow full 4-core usage for vision/numpy/OpenCV heavy ops
        OMP_NUM_THREADS: '4',
        OPENBLAS_NUM_THREADS: '4',
        MKL_NUM_THREADS: '4',
        NUMEXPR_NUM_THREADS: '4',
        GOMP_SPINCOUNT: '0',
        GPIOZERO_PIN_FACTORY: 'lgpio',
      },
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      error_file: '/home/pi/.robot/logs/cosmo-error.log',
      out_file: '/home/pi/.robot/logs/cosmo-out.log',
    },
  ]
};
