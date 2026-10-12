using System;
using System.Net;
using System.Text;

class Askpass
{
    static int Main(string[] args)
    {
        try
        {
            if (args.Length != 1 || (args[0].IndexOf("password", StringComparison.OrdinalIgnoreCase) < 0
                && args[0].IndexOf("passphrase", StringComparison.OrdinalIgnoreCase) < 0))
                return 1;
            int port = int.Parse(Environment.GetEnvironmentVariable("NPU_MONITOR_ASKPASS_PORT"));
            if (port < 1 || port > 65535) return 1;
            string token = Environment.GetEnvironmentVariable("NPU_MONITOR_ASKPASS_TOKEN");
            if (String.IsNullOrEmpty(token)) return 1;
            var request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + port + "/password");
            request.Proxy = null;
            request.Timeout = 3000;
            request.ReadWriteTimeout = 3000;
            request.AllowAutoRedirect = false;
            request.Headers.Add("X-Askpass-Token", token);
            using (var response = request.GetResponse())
            using (var reader = new System.IO.StreamReader(response.GetResponseStream(), Encoding.UTF8))
            {
                byte[] answer = Encoding.UTF8.GetBytes(reader.ReadToEnd() + "\n");
                using (var stdout = Console.OpenStandardOutput()) stdout.Write(answer, 0, answer.Length);
            }
            return 0;
        }
        catch
        {
            return 1;
        }
    }
}
